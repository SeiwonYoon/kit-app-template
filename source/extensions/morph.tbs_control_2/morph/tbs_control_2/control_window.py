# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
control_window.py — TBS 제어창 UI 및 이벤트 핸들러

【역할】
- build_control_window(ext): "TBS 제어창" 창. 최상단 화면 옵션(기본 메뉴/패널 숨기기),
  USD Load(TbsUsdWindow — extension.py),
  USD 타임라인(수동/자동), 가상 시그널 샘플,
  XML 제너레이터(6종 시퀀스 콤보·입력 필드), 우선 표시 접두사, prim 목록.
- refresh_object_list(ext): 드롭다운/목록 갱신.
- on_play_usd_animation / on_play_generator_sample / on_refresh_prim_list 등 버튼·콤보 핸들러.

【수정 포인트】
- USD 재생 UI: build_control_window() 상단 ~ "USD 애니메이션 정지" 버튼 근처.
- XML 제너레이터 UI: "XML 제너레이터 생성기" Frame 블록.
  · 콤보 항목 추가/순서 변경: ext._xml_seq_combo = ui.ComboBox(0, ...) 인자 목록.
  · 하단 입력 전환: on_xml_seq_changed — FROM/TO 보일지(ext._xml_ab_inputs_frame), PORT만 보일지(ext._xml_port_inputs_frame).
    → xml_generator.FROM_TO_SEQS / PORT_ID_ONLY_SEQS 와 동일한 규칙 유지.
  · OK/역파싱: on_xml_ok_clicked, on_xml_run_clicked — 내부 seqs 리스트는 콤보 순서와 반드시 일치.
- 애니메이션 버튼(예: 이동/포물선/회전): 파일 하단 on_* 및 SAMPLE_GENERATOR_JSON.

【이벤트→XML→역파싱→애니메이션(JSON) 상태 기반 룰 매핑 유지보수 가이드】
- 목적: 같은 이벤트라도 from/to/포트 점유 상태가 다르면 다른 JSON을 실행할 수 있게 한다.
- 규칙 파일(우선순위):
  1) `config/event_animation_rules.json`  ← 권장(상태 기반)
  2) `config/event_animation_map.json`    ← 기본 fallback(이벤트 단순 매핑)
- 포트별 LOT prim 가시성: `config/port_lot_prim_paths.json` — `port_lot_visibility.apply_port_lot_prim_visibility` (시뮬 이벤트마다)
- rules 형식(요약):
  · 리스트 항목: {"name","priority","when","use"}
  · when:
    - sequence: 정식 시퀀스명(예: EAPEIS_PORT_MOVE_TRANSFERING)
    - from_port / to_port / port: 문자열 일치
    - ports_occupancy: {"EP2":"FULL","BP3":"EMPTY"} 같은 상태 조건
      (값은 FULL/EMPTY 또는 특정 LOT_ID)
  · use:
    - json: 실행할 시퀀스 JSON 경로
    - runner / description: 부가 메타
- 호출 흐름(현재 구현의 주 경로):
  1) simulation_engine._emit_event()에서 payload + ports_occupancy 전달
  2) on_sim_start_clicked()의 on_event 콜백이 post_sim_anim_event(...) → 큐 SimUiQueueKind.ANIM_EVENT
  3) _drain_sim_log_queue() → _sim_ui_sink_anim_event → handle_sim_event_for_animation(ext, payload)
  4) handle_sim_event_for_animation():
     - payload를 canonical sequence(EAPEIS_PORT_*)로 정규화
     - xml_generator.build_xml_string(...)로 XML 생성
     - xml_generator.parse_xml_string(...)으로 역파싱
     - 역파싱 결과(sequence_name/from/to/port)를 rules/map 입력 payload로 표준화
  5) _resolve_event_animation_entry(seq, payload):
     - 먼저 rules에서 조건 매칭(우선순위 높은 규칙 우선)
     - 없으면 event_animation_map fallback
  6) _execute_mapped_sequence_stub(...)에서 파일 존재/파싱 검증 후 SequenceRunner로 즉시 실행 시도
- 유지보수 체크포인트:
  · XML 생성/역파싱 규칙 수정: `xml_generator.py` (상수/빌더/파서)
  · 이벤트 별칭(canonical 변환) 수정: `SIM_SEQ_ALIAS`
  · rules 조건 필드 확장: `_resolve_rule_entry`, `_matches_occupancy_rule`
  · JSON 실행 연결/예외처리 수정: `_execute_mapped_sequence_stub`
  · 최종 분기 로그/표시 메시지 수정: `handle_sim_event_for_animation`
- 시퀀스 편집기 JSON 연결 방법:
  1) 시퀀스 편집기에서 JSON 저장
  2) 파일을 extension 내부 경로(예: data/sim_sequences/*.json)에 배치
  3) rules 또는 map의 use.json 경로에 등록
  4) 시뮬레이션 이벤트 발생 시 자동 매칭/검증 로그 확인
 - 표시모드(SimLogPanelMode): 콤보 인덱스와 `_drain_sim_log_queue`의 이력 스킵 여부가 연동된다.
  · "둘다": 진행현황 + 이력로그
  · "진행현황": 진행현황만
  · "이력로그": 스토리/시뮬 이력만
 - 시뮬 UI 큐 라우팅: `SimUiQueueKind` + `_dispatch_sim_ui_queue_item` + `_sim_ui_sink_*`.
   새 공정 텍스트 로그는 `post_sim_history_line(ext, line)`(시뮬 스레드)만 쓰면 이력 창으로 간다.
 - 시뮬레이션 종료 시 `_export_sim_logs_to_xlsx()`가 자동 호출되어
   `data/sim_logs/sim_logs_YYYYmmdd_HHMMSS.xlsx`에 **JSON 타임테이블만** 저장한다.
   (시트는 한 장(`타임테이블`)으로 통합, 1열에 JSON 라인을 시간순으로 한 줄씩 기록.
   화면 구분은 각 라인의 `"screen"` 키로 한다.)

【시뮬레이션 이벤트→애니메이션(JSON) 매핑 요약 (요구사항 반영)】
주의:
- 실제 실행 우선순위는 `EVENT_JSON_CASE_MAP` → rules(`config/event_animation_rules.json`) → map(`config/event_animation_map.json`) 순이다.
- 아래 목록은 코드 내 기본 테이블 `EVENT_JSON_CASE_MAP` 기준이며, payload의 키는 seq + (from_port_id/to_port_id 또는 port_id)이다.
- 포트 ID: **INOUT**(IN/OUT), 버퍼 **BP1~BP4**(EP3 구성일 때만 BP4 사용), **EP1~EP3**.

1) 생성/투입(OHT 운반) — 애니는 ARRIVED에서만
- **이벤트(sequence_name)**: `EAPEIS_PORT_ARRIVED`
- **조건·케이스맵 키**
  - OHT→EP 직접 투입: `from_port_id="OHT"`, `to_port_id="EP1|EP2|EP3"` → 키 `OHT->EPn`
  - OHT→IN/OUT 안착: `port_id="INOUT"` 만 전달(from/to 없음) → 키 `INOUT`
- **JSON**
  - `arrived_inout.json` — IN/OUT 안착
  - `arrived_ep1.json` … `arrived_ep3.json` — EP 직접 투입

2) IN/OUT → 버퍼(BP) 이동 — 애니 실행
- **이벤트(sequence_name)**: `EAPEIS_PORT_MOVE_TRANSFERING`
- **조건**: `from_port_id="INOUT"`, `to_port_id="BP1|BP2|BP3|BP4"`
- **JSON**: `move_inout_bp1.json` … `move_inout_bp4.json` (도착 버퍼별)

3) 버퍼 → EP(공정포트) 이동 — 애니 실행
- **이벤트(sequence_name)**: `EISEAP_PORT_MOVE_REQ`
- **조건**: `from_port_id="BP1|BP2|BP3|BP4"`, `to_port_id="EP1|EP2|EP3"`
- **JSON**: `move_{버퍼}_{EP}.json` (예: `move_bp1_ep1.json`, `move_bp4_ep3.json`)

4) 회수 우선 실행 — 애니는 REMOVED에서만
- **이벤트(sequence_name)**: `EAPEIS_PORT_REMOVED`
- **조건**: `port_id="EP1|EP2|EP3"`
- **JSON**
  - EP1: `data/sim_sequences/removed_ep1.json`
  - EP2: `data/sim_sequences/removed_ep2.json`
  - EP3: `data/sim_sequences/removed_ep3.json`

5) 애니 없는 이벤트(상태/큐 의미만)
- `EAPEIS_PORT_READYTOLOAD` (생성/수신 준비)
- `EAPEIS_PORT_READYTOUNLOAD` (회수 요청 큐 적재)

【XML 시퀀스와 UI 필드】 (로직·상수는 xml_generator.py)
- FROM_PORT_ID + TO_PORT_ID: MOVE_TRANSFERING, MOVE, MOVE_REQ
- PORT_ID만: READYTOLOAD, ARRIVED, READYTOUNLOAD, REMOVED
새 종류 추가 시: xml_generator 수정 + 이 파일의 ComboBox·seqs 3곳 + 필요 시 IntField/모델 추가.

【주요 함수 색인(빠른 참조)】
- 경로·규칙 파일: _extension_root_dir(확장 루트), _event_animation_map_path / _event_animation_rules_path(JSON 경로),
  _load_event_animation_map·_load_event_animation_rules(mtime 캐시·로드)
- 규칙 매칭: _matches_occupancy_rule(ports_occupancy 조건), _resolve_rule_entry(rules.json 우선순위),
  _resolve_event_case_map_entry(EVENT_JSON_CASE_MAP), _resolve_event_animation_entry(통합: case→rules→map),
  _normalize_json_path(상대→절대)
- 실행: _execute_mapped_sequence_stub(매핑된 JSON 검증·SequenceRunner 실행), _estimate_step_duration_sec_for_log,
  _estimate_sequence_total_duration_sec_for_log, _estimate_anim_duration_for_gate_payload(게이트 대기 시간 추정)
- 이벤트 처리: handle_sim_event_for_animation(시뮬 payload→XML→역파싱→룰→JSON 실행), _on_sim_event(로그용 래퍼)
- 시뮬 UI 큐(스레드→UI): post_sim_history_line, post_sim_anim_event, post_sim_progress_update,
  _enqueue_sim_log·_enqueue_anim_event·_enqueue_control_action·_enqueue_gate_request·_enqueue_sim_progress,
  _drain_sim_log_queue(메인 스레드에서 소비), _dispatch_sim_ui_queue_item, _coerce_sim_ui_queue_kind,
  _sim_ui_sink_progress·_sim_ui_sink_anim_event·_sim_ui_sink_history_line·_sim_ui_sink_action·_sim_ui_sink_gate
- 게이트: _show_sim_gate_dialog, _close_sim_gate_dialog, on_sim_start_clicked 내부 on_gate 연동
- 진행·로그 UI: _append_sim_log, _format_history_line·_with_history_color_icon, _append_anim_history_log(노옵),
  _render_pending_dots,
  _update_sim_progress, _is_progress_only_mode, on_copy_sim_progress
- 포트 패널: _port_cell_text, _compact_cell_value, _sync_ep3_port_cell_visibility, _set_port_box_style, _update_port_occupancy_panel
- 시뮬 제어: on_sim_start_clicked·on_sim_stop_clicked·on_sim_reset_clicked, _detach_sim_update,
  on_sim_log_view_changed, on_sim_ep_count_changed, _export_sim_logs_to_xlsx
- XML UI: on_xml_seq_changed, on_xml_ok_clicked, on_xml_run_clicked
- 포트 문자열: _parse_port_num, _port_kind, _normalize_port_text_from_xml
- Prim 목록: on_refresh_prim_list, refresh_object_list, build_object_panel, on_button_0/1/2
- 가상 시그널: receive_signal_data, run_generator_from_parsed
- 창: build_control_window(전체 UI 조립)

【멀티 뷰포트 분할·화면별 시뮼 독립 진행 (유지보수 가이드)】
개요:
- **뷰포트 분할(2~4)**: `sim_multi_view.apply_sim_viewport_split_layout` 이 Kit Viewport/보조 창·USD 컨텍스트를 구성한다.
  제어창은 `ext._sim_viewport_split_count` 만 “실제 적용된 분할 수”로 유지하고, 체크박스·모니터 UI와 동기화한다.
- **시뮼 엔진**: 분할 수가 N>1 이면 `TBSSimulationEngine` 인스턴스를 N개 만들고 `ext._sim_engines` 에 넣는다. 각 엔진은
  `event_tags={"tbs_sim_screen": "1".."N"}` 로 로그·진행·게이트 payload에 화면 번호를 붙인다.
- **독립 tick**: N>1 일 때 **화면마다 별도 스레드**(`_sim_multi_engine_tick_worker`)에서 `sim.tick()` 만 호출한다.
  한 화면이 공정 확인(`on_gate` → `done_evt.wait()`)에 블로킹돼도 다른 화면 스레드는 계속 진행한다.
- **애니·pause와의 관계**: JSON `SequenceRunner`·Kit translate/rotate/curve 가 돌 때 `_sim_tick_pause_event` 가 켜질 수 있다.
  멀티일 때는 `_multi_tick_should_skip_for_screen` 으로 **해당 화면만** tick 을 잠시 건너뛰어, 다른 화면 sim 시간은 진행시킨다.
  애니 job 에는 `tbs_sim_screen` 이 들어가 `_sim_active_anim_owner_screen` 이 “어느 화면용 pause 인지”를 판별한다.
- **애니 재생 경로**: `_sim_ui_sink_anim_event` → `handle_sim_event_for_animation` → JSON `SequenceRunner`
  (`usd_context_name` 으로 분할 보조 스테이지에 MOVE 등 적용).

주요 ext 필드:
- `_sim_viewport_split_count` : 1~4, sim_multi_view 와 제어창 체크박스의 단일 소스.
- `_sim_engines` / `_sim_engine` : 멀티 시 엔진 리스트·호환용 [0] 참조.
- `_sim_tick_threads` : 멀티 시 worker 스레드들; `_detach_sim_update` 에서 모두 join.
- `_sim_thread` / `_sim_thread_stop` : 단일 채널용 기존 tick 스레드; 멀티 시 `_sim_thread` 는 None 일 수 있음.
- `_sim_per_screen_snapshots` : 화면별 시뮼 설정 스냅샷(저장/미저장); `_on_save_sim_settings_to_screen` 으로 갱신.
- `_sim_monitor_channels` : 분할 모니터(포트/진행/로그) 컬럼 dict 리스트; `_rebuild_sim_monitor_split_ui` 가 조립.
- `_sim_multi_export_done` / `_sim_multi_tick_shutdown` : 멀티 전체 종료·엑셀 export 한 번만.

함수별 역할 (분할·멀티 시뮼 직접 관련):
- `_refresh_sim_per_screen_status_labels` : 스냅샷 유무에 따라 화면별 “(저장됨)/(미저장)” 라벨 갱신.
- `_refresh_sim_per_screen_rows` : 분할 수에 맞춰 화면별 설정 행(HStack) visible 처리.
- `_on_save_sim_settings_to_screen` : 현재 제어창 값을 해당 화면 스냅샷에 저장·EP3 셀·HUD 스케줄.
- `_auto_fill_per_screen_snapshots_on_start` : 시뮼 시작 시 비어 있는 화면 슬롯에 현재 설정 복사.
- `_fault_ports_from_snapshot` / `_timing_and_init_from_snapshot` : 스냅샷→엔진용 고장 집합·Timing/InitConfig.
- `_sync_sim_multi_split_row_visibility` : USD 로드 후에만 분할 설정 행 표시.
- `_sync_sim_split_checkboxes_from_ext_count` : `ext` 분할 수와 1~4 체크박스 모델 동기화(재진입 가드).
- `_force_sim_split_to_default` : 분할 불가·스테이지 없을 때 1화면·스냅샷 초기화·레이아웃 1로 롤백.
- `_on_sim_split_choice_changed` : 사용자가 분할 체크박스 변경 시 상호 배타 처리 후 `apply_sim_viewport_split_layout`.
- `_usd_context_name_for_sim_screen` : 애니/가시성용 보조 USD 컨텍스트 이름(`morph_tbs_split_aux_*`).
- `_sim_monitor_channel_count` : 모니터 열 개수(분할 수와 동일).
- `_snapshot_monitor_channel_texts` / `_create_sim_monitor_channel_column` / `_rebuild_sim_monitor_split_ui` :
  분할 모니터 UI 재구축 및 이전 채널 텍스트 보존.
- `_sim_active_anim_owner_screen` : `_sim_anim_active["tbs_sim_screen"]` 기반 pause 대상 화면(1-based).
- `_multi_tick_should_skip_for_screen` : pause 이벤트 시 **이번 프레임에 tick 을 건너뛸 화면** 판정.
- `_sim_multi_engine_tick_worker` : 한 엔진 전용 tick 루프 스레드; 전 엔진 종료 시 export 액션 enqueue.
- `_detach_sim_update` : tick 스레드(복수 우선) 정지·join, 로그 UI 구독 해제.
- `on_sim_start_clicked` : N채널 엔진 생성·콜백 연결·N>1 이면 worker 스레드 기동, N==1 이면 기존 단일 `_tick_loop`.
- `_tick_loop` (내부 함수) : 단일/구조상 단일 스레드에서 모든 엔진 순차 tick(레거시 멀티 경로는 worker로 대체됨).
- `_execute_mapped_sequence_stub` : 이벤트→JSON 실행; job 에 `tbs_sim_screen` 포함(멀티 pause 판별용).
- `_sim_ui_sink_anim_event` : 큐에서 소비; 화면별 가시성 + 이벤트→JSON(화면별 USD 컨텍스트).
- `_update_sim_progress` : `tbs_sim_screen` 으로 멀티 진행현황 라벨 라우팅.

사용처: extension.py on_startup → build_control_window(self)
  · 재호출 시 기존 TBS 제어창은 destroy 후 재생성(확장 리로드 등으로 위젯 이중 생성 방지).
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import copy
import random
import threading
import time
import queue
import json
import re
from datetime import datetime
from pathlib import Path

import omni.kit.app as app
import omni.ui as ui
from pxr import Gf

from . import usd_animation_control
from . import xml_generator
from .curve_animation import (
    is_curve_animation_running,
    make_parabolic_path,
    run_prim_curve_animation,
    stop_all_curve_animations,
    stop_prim_curve_animation,
)
from .kit_chrome_visibility import KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH, apply_kit_chrome_hidden
from .port_lot_visibility import (
    apply_port_lot_prim_visibility,
    apply_port_lot_prim_visibility_for_context,
    clear_port_lot_authoring_cache,
)
from .prim_info import get_prim_display_name, safe_str
from .prim_utils import (
    collect_prim_paths_safe,
    find_all_prim_paths_by_name,
    get_prim_local_translate,
    get_stage,
    is_usd_file_stage_loaded,
    set_prim_translate_only,
)
from .rotate_animation import (
    is_rotate_animation_running,
    run_prim_rotate_animation,
    stop_all_rotate_animations,
    stop_prim_rotate_animation,
)
from .selection_overlay import show_prim_info_in_viewport
from .signal_parser import parse_signal
from .sequence_engine import SequenceRunner
from . import sim_multi_view
from .simulation_engine import (
    Lot,
    SimulationInitConfig,
    SimulationLogConfig,
    SimulationTimingConfig,
    TBSSimulationEngine,
)
from .translate_animation import (
    is_translate_animation_running,
    run_prim_translate_animation,
    stop_all_translate_animations,
    stop_prim_translate_animation,
)

from .control_sim_prerun_playback import (
    PlaybackEngine,
    SimPreRunResult,
    SimTimelinePlayer,
    prerun_engine_to_timeline,
)

MAX_PRIMS_DISPLAY = 80
DEFAULT_PRIORITY_NAME_PREFIX = "Mesh_"
CHECKBOX_WHITE_STYLE = {
    "color": 0xFF000000,
    "background_color": 0xFFEEEEEE,
}


class SimUiQueueKind(str, Enum):
    """
    `_sim_log_queue` 튜플 (kind, payload) 의 kind.
    payload가 도달하는 UI 영역은 아래 sink와 1:1에 가깝게 대응한다.
    """

    PROGRESS = "progress"  # → 진행현황 패널 (_update_sim_progress)
    HISTORY_LINE = "log"  # → 이력 로그 패널 (_append_sim_log). 값 "log"는 기존 큐 호환 유지.
    ANIM_EVENT = "anim_event"  # → 포트 상태 + 시퀀스 실행 (handle_sim_event_for_animation)
    ACTION = "action"  # → 제어 액션 (예: xlsx보내기)
    GATE = "gate"  # → 공정 확인 창


class SimUiControlAction(str, Enum):
    """SimUiQueueKind.ACTION 의 payload 로 허용되는 값."""

    EXPORT_XLSX = "export_xlsx"


class SimLogPanelMode(int, Enum):
    """표시모드 콤보 인덱스 (`on_sim_log_view_changed` 와 동일)."""

    ALL = 0
    PROGRESS_ONLY = 1
    HISTORY_ONLY = 2


SIM_SEQ_ALIAS = {
    "READYTOLOAD": xml_generator.SEQ_READYTOLOAD,
    "ARRIVED": xml_generator.SEQ_ARRIVED,
    "MOVE_TRANSFERING": xml_generator.SEQ_MOVE_TRANSFERING,
    "MOVE": xml_generator.SEQ_MOVE,
    "MOVE_REQ": xml_generator.SEQ_MOVE_REQ,
    "READYTOUNLOAD": xml_generator.SEQ_READYTOUNLOAD,
    "REMOVED": xml_generator.SEQ_REMOVED,
}
# 이벤트별/포트별 JSON 매핑(최상단 일원화)
# - 운영 중 수정은 이 테이블을 우선 수정한다.
# - key 규칙:
#   * READYTOLOAD/READYTOUNLOAD: 애니 없음(매핑 비워둠)
#   * ARRIVED: OHT 이동 애니만 실행 → key="FROM->TO"(직접 EP) 또는 port만 있으면 key=port(INOUT 안착)
#   * EAPEIS_PORT_MOVE_TRANSFERING: INOUT->BPx 이동 애니만 실행 → key="FROM->TO"
#   * EISEAP_PORT_MOVE_REQ: BPx->EPy 이동 애니만 실행 → key="FROM->TO"
#   * REMOVED: 회수 우선순위가 되었을 때 회수 애니 실행 → key="PORT" (EP1/2/3)
EVENT_JSON_CASE_MAP: Dict[str, Dict[str, str]] = {
    # IN/OUT → 버퍼: 시뮬 payload from=INOUT, to=BP1..BP4
    xml_generator.SEQ_MOVE_TRANSFERING: {
        "INOUT->BP1": "data/sim_sequences/move_inout_bp1.json",
        "INOUT->BP2": "data/sim_sequences/move_inout_bp2.json",
        "INOUT->BP3": "data/sim_sequences/move_inout_bp3.json",
        "INOUT->BP4": "data/sim_sequences/move_inout_bp4.json",
    },
    xml_generator.SEQ_ARRIVED: {
        # OHT→EP 직접: FROM->TO / OHT→IN/OUT 안착: port_id만 → 키 INOUT
        "INOUT": "data/sim_sequences/arrived_inout.json",
        "OHT->EP1": "data/sim_sequences/arrived_ep1.json",
        "OHT->EP2": "data/sim_sequences/arrived_ep2.json",
        "OHT->EP3": "data/sim_sequences/arrived_ep3.json",
    },
    # 요구사항: READYTOLOAD / READYTOUNLOAD 는 애니 실행 안함(빈 dict 유지)
    xml_generator.SEQ_READYTOLOAD: {},
    xml_generator.SEQ_READYTOUNLOAD: {},
    # 요구사항: BP->EP 이동 애니는 EISEAP_PORT_MOVE_REQ 에서만 실행
    xml_generator.SEQ_MOVE_REQ: {
        "BP1->EP1": "data/sim_sequences/move_bp1_ep1.json",
        "BP1->EP2": "data/sim_sequences/move_bp1_ep2.json",
        "BP1->EP3": "data/sim_sequences/move_bp1_ep3.json",
        "BP2->EP1": "data/sim_sequences/move_bp2_ep1.json",
        "BP2->EP2": "data/sim_sequences/move_bp2_ep2.json",
        "BP2->EP3": "data/sim_sequences/move_bp2_ep3.json",
        "BP3->EP1": "data/sim_sequences/move_bp3_ep1.json",
        "BP3->EP2": "data/sim_sequences/move_bp3_ep2.json",
        "BP3->EP3": "data/sim_sequences/move_bp3_ep3.json",
        "BP4->EP1": "data/sim_sequences/move_bp4_ep1.json",
        "BP4->EP2": "data/sim_sequences/move_bp4_ep2.json",
        "BP4->EP3": "data/sim_sequences/move_bp4_ep3.json",
    },
    xml_generator.SEQ_REMOVED: {
        "EP1": "data/sim_sequences/removed_ep1.json",  # EP1에서 LOT/Foup 회수 완료 연출
        "EP2": "data/sim_sequences/removed_ep2.json",  # EP2에서 LOT/Foup 회수 완료 연출
        "EP3": "data/sim_sequences/removed_ep3.json",  # EP3에서 LOT/Foup 회수 완료 연출
    },
}

def _case_map_port_token(p: str) -> str:
    """이벤트 payload 포트 문자열을 케이스맵 키용으로 정규화(대문자·트림)."""
    return str(p or "").strip().upper()
SAMPLE_GENERATOR_JSON = """{
  "objects": ["Mesh_308", "Mesh_561", "WalkwayEndA_01"],
  "animation": {
    "segments": [
      {"duration": 1.0, "delta": [100, 0, 0]},
      {"duration": 1.0, "delta": [0, 100, 0]},
      {"duration": 2.0, "delta": [-100, -100, 0]}
    ]
  }
}"""

_EVENT_ANIM_MAP_CACHE: Optional[Dict[str, Any]] = None
_EVENT_ANIM_MAP_MTIME: Optional[float] = None
_EVENT_ANIM_RULES_CACHE: Optional[List[Dict[str, Any]]] = None
_EVENT_ANIM_RULES_MTIME: Optional[float] = None


def _extension_root_dir() -> Path:
    """이 파일 기준 확장 루트(.../morph.tbs_control_2). config·data 경로 계산에 사용."""
    # .../source/extensions/morph.tbs_control_2
    return Path(__file__).resolve().parents[2]


def _sequence_json_search_roots() -> Tuple[Path, ...]:
    """
    ``data/sim_sequences/*.json`` 을 찾을 때 쓸 확장 패키지 루트 후보(앞쪽 우선).

    Kit 가 확장을 빌드 산출물/캐시에서만 로드하고 ``data`` 폴더는 소스 트리에만 둔 경우,
    ``__file__`` 기준 단일 루트로는 JSON 이 "없음"으로 판정될 수 있다.
    """
    roots: List[Path] = []
    seen: Set[str] = set()

    def _add(path: Optional[Path]) -> None:
        if path is None:
            return
        try:
            rp = path.resolve()
        except Exception:
            rp = Path(path)
        key = str(rp).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(rp)

    try:
        import carb

        rt = carb.tokens.get_tokens_interface().resolve("${root}")
        if rt:
            src = Path(rt) / "source" / "extensions" / "morph.tbs_control_2"
            if (src / "data" / "sim_sequences").is_dir():
                _add(src)
    except Exception:
        pass

    _add(Path(__file__).resolve().parents[2])

    try:
        em = app.get_app().get_extension_manager()
        for ext in em.get_extensions() or []:
            if not isinstance(ext, dict) or not ext.get("enabled"):
                continue
            eid = str(ext.get("id", "") or "")
            if eid != "morph.tbs_control_2" and not eid.startswith("morph.tbs_control_2-"):
                continue
            epath = em.get_extension_path(eid)
            if not epath:
                continue
            pp = Path(epath)
            if (pp / "data" / "sim_sequences").is_dir():
                _add(pp)
    except Exception:
        pass

    return tuple(roots)


def _event_animation_map_path() -> Path:
    """이벤트 seq → JSON 단순 매핑 파일 경로."""
    return _extension_root_dir() / "config" / "event_animation_map.json"


def _event_animation_rules_path() -> Path:
    """상태 기반 애니메이션 규칙(우선순위 리스트) 파일 경로."""
    return _extension_root_dir() / "config" / "event_animation_rules.json"


def _load_event_animation_map() -> Dict[str, Any]:
    """event_animation_map.json을 읽어 dict로 반환. mtime이 같으면 캐시 재사용."""
    global _EVENT_ANIM_MAP_CACHE, _EVENT_ANIM_MAP_MTIME
    p = _event_animation_map_path()
    if not p.exists():
        _EVENT_ANIM_MAP_CACHE = {}
        _EVENT_ANIM_MAP_MTIME = None
        return {}
    try:
        mtime = p.stat().st_mtime
        if _EVENT_ANIM_MAP_CACHE is not None and _EVENT_ANIM_MAP_MTIME == mtime:
            return _EVENT_ANIM_MAP_CACHE
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
        _EVENT_ANIM_MAP_CACHE = data
        _EVENT_ANIM_MAP_MTIME = mtime
        return data
    except Exception as e:
        print(f"[ANIM MAP] 매핑 파일 로드 실패: {p} err={e}", flush=True)
        _EVENT_ANIM_MAP_CACHE = {}
        _EVENT_ANIM_MAP_MTIME = None
        return {}


def _load_event_animation_rules() -> List[Dict[str, Any]]:
    """event_animation_rules.json을 읽어 규칙 리스트로 반환(priority 오름차순 정렬, mtime 캐시)."""
    global _EVENT_ANIM_RULES_CACHE, _EVENT_ANIM_RULES_MTIME
    p = _event_animation_rules_path()
    if not p.exists():
        _EVENT_ANIM_RULES_CACHE = []
        _EVENT_ANIM_RULES_MTIME = None
        return []
    try:
        mtime = p.stat().st_mtime
        if _EVENT_ANIM_RULES_CACHE is not None and _EVENT_ANIM_RULES_MTIME == mtime:
            return _EVENT_ANIM_RULES_CACHE
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            data = []
        norm: List[Dict[str, Any]] = [x for x in data if isinstance(x, dict)]
        norm.sort(key=lambda r: int(r.get("priority", 1000)))
        _EVENT_ANIM_RULES_CACHE = norm
        _EVENT_ANIM_RULES_MTIME = mtime
        return norm
    except Exception as e:
        print(f"[ANIM RULES] 규칙 파일 로드 실패: {p} err={e}", flush=True)
        _EVENT_ANIM_RULES_CACHE = []
        _EVENT_ANIM_RULES_MTIME = None
        return []


def _matches_occupancy_rule(rule_occ: Dict[str, Any], occ: Dict[str, Any]) -> bool:
    """규칙의 ports_occupancy(포트→FULL/EMPTY/LOT_ID)가 현재 occ 스냅샷과 일치하는지."""
    for port, expected in (rule_occ or {}).items():
        p = str(port).strip().upper()
        got = str((occ or {}).get(p, "") or "")
        exp = str(expected or "").strip()
        if exp.upper() == "FULL":
            if not got:
                return False
        elif exp.upper() == "EMPTY":
            if got:
                return False
        else:
            if got != exp:
                return False
    return True


def _resolve_rule_entry(seq: str, payload: Dict[str, str]) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    rules = _load_event_animation_rules()
    if not rules:
        return (None, None, None)
    p_from = str(payload.get("from_port_id", "") or "")
    p_to = str(payload.get("to_port_id", "") or "")
    p_port = str(payload.get("port_id", "") or "")
    occ = payload.get("ports_occupancy", {}) if isinstance(payload.get("ports_occupancy", {}), dict) else {}

    for r in rules:
        when = r.get("when", {}) if isinstance(r.get("when", {}), dict) else {}
        if str(when.get("sequence", "") or "").strip() not in ("", seq):
            continue
        if str(when.get("from_port", "") or "").strip() not in ("", p_from):
            continue
        if str(when.get("to_port", "") or "").strip() not in ("", p_to):
            continue
        if str(when.get("port", "") or "").strip() not in ("", p_port):
            continue
        rule_occ = when.get("ports_occupancy", {})
        if isinstance(rule_occ, dict) and not _matches_occupancy_rule(rule_occ, occ):
            continue

        use = r.get("use", {}) if isinstance(r.get("use", {}), dict) else {}
        j = use.get("json")
        if isinstance(j, str) and j.strip():
            return (j.strip(), use, str(r.get("name", "")))
    return (None, None, None)


def _resolve_event_case_map_entry(seq: str, payload: Dict[str, str]) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    table = EVENT_JSON_CASE_MAP.get(seq, {})
    if not isinstance(table, dict) or not table:
        return (None, None, None)
    p_from = _case_map_port_token(str(payload.get("from_port_id", "") or ""))
    p_to = _case_map_port_token(str(payload.get("to_port_id", "") or ""))
    p_port = _case_map_port_token(str(payload.get("port_id", "") or ""))
    key = f"{p_from}->{p_to}" if p_from and p_to else p_port
    if not key:
        return (None, None, None)
    j = table.get(key)
    if not isinstance(j, str) or not j.strip():
        return (None, None, None)
    meta = {
        "runner": "sequence_editor",
        "description": f"top-case-map:{key}",
    }
    return (j.strip(), meta, f"top_case_map:{seq}:{key}")


def _resolve_event_animation_entry(seq: str, payload: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str], str]:
    """
    반환:
    - json_path_str: 실제 JSON 경로 문자열(없으면 None)
    - meta: runner/description 등 부가정보
    """
    # 0) 파일 최상단 케이스 매핑(운영 우선)
    j_case, meta_case, case_name = _resolve_event_case_map_entry(seq, payload or {})
    if j_case:
        return (j_case, meta_case, case_name, "top_case_map")

    # 1) 상태 기반 rules 우선
    j_rule, meta_rule, rule_name = _resolve_rule_entry(seq, payload or {})
    if j_rule:
        return (j_rule, meta_rule, rule_name or "(unnamed-rule)", "rules")

    # 2) 기존 단순 map fallback
    m = _load_event_animation_map()
    if not m:
        return (None, None, None, "")

    # 키 우선순위:
    # 1) 정식 seq (EAPEIS_PORT_...)
    # 2) 별칭(READYTOLOAD 등)
    # 3) raw 값
    raw_alias = None
    for alias, canonical in SIM_SEQ_ALIAS.items():
        if canonical == seq:
            raw_alias = alias
            break

    cand = [seq]
    if raw_alias:
        cand.append(raw_alias)

    for key in cand:
        if key not in m:
            continue
        v = m.get(key)
        if isinstance(v, str):
            return (v, {"runner": "sequence_editor", "description": ""}, None, "map")
        if isinstance(v, dict):
            j = v.get("json")
            if isinstance(j, str) and j.strip():
                return (j.strip(), v, None, "map")
    return (None, None, None, "")


def _normalize_json_path(path_text: str) -> Path:
    """
    시퀀스 JSON 상대 경로를 확장 루트 기준 절대 Path 로.

    후보 루트(``_sequence_json_search_roots``)를 순서대로 보며 ``.is_file()`` 인 첫 경로를 반환한다.
    없으면 기존과 같이 ``_extension_root_dir()`` 기준 경로를 반환(오류 메시지·로그용).
    """
    raw = str(path_text or "").strip()
    p = Path(raw)
    if not raw:
        return p
    if p.is_absolute():
        try:
            return p.resolve()
        except Exception:
            return p
    rel = p
    roots = _sequence_json_search_roots()
    last: Optional[Path] = None
    for root in roots:
        try:
            cand = (root / rel).resolve()
        except Exception:
            cand = root / rel
        last = cand
        try:
            if cand.is_file():
                return cand
        except Exception:
            pass
    if last is not None:
        return last
    base = roots[0] if roots else _extension_root_dir()
    try:
        return (base / rel).resolve()
    except Exception:
        return base / rel


def _estimate_step_duration_sec_for_log(step: Dict[str, Any], *, speed_scale: float = 1.0) -> Optional[float]:
    """
    애니메이션 실행이력용 "예상 길이" 계산(보수적).
    - MOVE/ROTATE: duration_max가 있으면 max, 없으면 duration.
    - DELAY: duration
    - USD_TIMELINE: 프레임 범위(start/end)로 추정
    """
    # NOTE(정책):
    # - "표시/예상 시간"은 **1배속 기준(콘텐츠 기준)** 으로 유지한다.
    # - 시뮬 배속(ext._sim_speed_model)은 "재생/진행 속도"만 바꾸고, 여기의 표기 시간에는 반영하지 않는다.
    # - 단, USD_TIMELINE의 per-step 배속(step["speed_scale"])은 "그 스텝 자체를 빠르게 재생"하므로 표기에도 반영한다.
    try:
        t = str((step or {}).get("type") or "").upper()
    except Exception:
        return None
    try:
        if t in ("MOVE", "ROTATE"):
            if "duration_max" in (step or {}):
                return max(0.0, float((step or {}).get("duration_max", (step or {}).get("duration", 0.0))))
            return max(0.0, float((step or {}).get("duration", 0.0)))
        if t == "DELAY":
            return max(0.0, float((step or {}).get("duration", 0.0)))
        if t == "USD_TIMELINE":
            start = int((step or {}).get("start_frame", 0))
            end = int((step or {}).get("end_frame", 0))
            if end <= start:
                return 0.0
            # 정책: 기본 30fps(TPS) 기반 환산 + 배속 반영
            try:
                step_sp = float((step or {}).get("speed_scale", 1.0))
            except Exception:
                step_sp = 1.0
            step_sp = max(0.01, float(step_sp))
            base = float(usd_animation_control.frame_to_time(float(end - start)))
            return max(0.0, base / float(step_sp))
    except Exception:
        return None
    return None


def _estimate_sequence_total_duration_sec_for_log(steps: List[Dict[str, Any]], *, speed_scale: float = 1.0) -> Optional[float]:
    """
    SequenceRunner의 그룹/지연 규칙을 단순화해서 "예상 총 길이"를 계산한다.
    - 병렬 그룹: 리더 시작 시각 기준으로 (offset + duration)의 최대값을 그룹 종료로 본다.
    - 다음 그룹 시작: engine과 동일하게 anchor_end + next.step_delay_ms 를 사용하되,
      그룹 시작(t0)보다 앞당기지는 않는다.
    """
    if not steps:
        return 0.0
    # 첫 스텝의 step_delay_ms는 시퀀스 시작 전 지연으로 해석
    try:
        t_cursor = max(0.0, int((steps[0] or {}).get("step_delay_ms", 0)) / 1000.0)
    except Exception:
        t_cursor = 0.0
    last_finish = t_cursor

    i = 0
    while i < len(steps):
        try:
            g_end = _group_end_index(steps, i)
        except Exception:
            g_end = i
        t0 = t_cursor

        # 그룹 내 예상 종료(병렬 최대)
        group_finish = t0
        for j in range(i, g_end + 1):
            st = steps[j] if isinstance(steps[j], dict) else {}
            off = 0.0
            if j != i:
                try:
                    off = max(0.0, int((st or {}).get("step_delay_ms", 0)) / 1000.0)
                except Exception:
                    off = 0.0
            # 표기/예상 시간은 1배속 기준(콘텐츠 기준).
            # (USD_TIMELINE step["speed_scale"]만 _estimate_step_duration_sec_for_log 내부에서 반영)
            dur = _estimate_step_duration_sec_for_log(st, speed_scale=1.0)
            if dur is None:
                # 알 수 없는 타입/auto 타임라인이 섞이면 전체 추정도 None 처리
                return None
            group_finish = max(group_finish, t0 + off + float(dur))
        last_finish = max(last_finish, group_finish)

        next_idx = g_end + 1
        if next_idx >= len(steps):
            break

        # anchor 종료 시각(앵커 스텝은 그룹 마지막)
        anchor_step = steps[g_end] if isinstance(steps[g_end], dict) else {}
        anchor_off = 0.0
        if g_end > i:
            try:
                anchor_off = max(0.0, int((anchor_step or {}).get("step_delay_ms", 0)) / 1000.0)
            except Exception:
                anchor_off = 0.0
        anchor_dur = _estimate_step_duration_sec_for_log(anchor_step, speed_scale=1.0)
        if anchor_dur is None:
            return None
        anchor_end = t0 + anchor_off + float(anchor_dur)

        try:
            delay_next = int((steps[next_idx] or {}).get("step_delay_ms", 0)) / 1000.0
        except Exception:
            delay_next = 0.0
        t_cursor = max(t0, anchor_end + float(delay_next))
        i = next_idx

    return max(0.0, float(last_finish))


def _execute_mapped_sequence_stub(
    ext: Any,
    seq: str,
    payload: Dict[str, str],
    json_path_text: str,
    meta: Optional[Dict[str, Any]],
    rule_name: Optional[str],
    verbose: bool,
) -> None:
    """
    rules/map이 가리키는 JSON을 검증한 뒤 SequenceRunner.run()으로 실제 재생한다.

    - 시뮬 tick: 배속>1 등에서 ``_sim_tick_pause_event`` 로 잠시 맞출 수 있다. 멀티 뷰에서는 job 의
      ``tbs_sim_screen``(``payload`` 의 ``tbs_sim_screen``)으로 **어느 화면의 tick 만** 멈출지 판별한다.
    - JSON 시퀀스(SequenceRunner) 재생 중에는 단일 스레드 모드에서는 tick 을 막지 않아 공정과 병행될 수 있다.
    - 동시 ``run()`` 방지를 위해 ``_sim_anim_pending`` 큐로 직렬화한다.
    """
    p = _normalize_json_path(json_path_text)
    runner = str((meta or {}).get("runner", "sequence_editor"))
    desc = str((meta or {}).get("description", ""))
    from_port = str(payload.get("from_port_id", "")).strip()
    to_port = str(payload.get("to_port_id", "")).strip()
    port = str(payload.get("port_id", "")).strip()
    if from_port and to_port:
        route = f"{from_port}->{to_port}"
    elif to_port:
        route = f"to={to_port}"
    elif from_port:
        route = f"from={from_port}"
    elif port:
        route = f"port={port}"
    else:
        route = "port=미상"
    base_desc = desc if desc else "동작설명 없음"
    lot_id = str(payload.get("lot_id", "")).strip() or "-"
    action_text = f"{base_desc} ({route} | lot={lot_id})"
    sim_time = str(payload.get("sim_time", "")).strip()
    # 스토리-JSON 요약(애니 실행이력 창) 기록: "실행/스킵/실패" 모두 남겨야 누적이 끊기지 않는다.
    def _push_story_json(status: str) -> None:
        """(애니 실행이력 UI 제거) 스토리-JSON 요약 누적은 더 이상 사용하지 않는다."""
        return

    if not p.is_file():
        _push_story_json("SKIP (MISSING)")
        _append_anim_history_log(
            ext,
            f"[ANIM] 파일없음 | event={seq} | action={action_text} | need={p.name} | path={p}",
        )
        if verbose:
            print(
                f"[ANIM MAP] 이벤트={seq} -> JSON 파일 없음: {p} "
                f"(runner={runner}, rule={rule_name or '-'}, desc={desc})",
                flush=True,
            )
        return
    try:
        # 파일 유효성 확인(실행 전 파싱 검증)
        parsed = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(parsed, list):
            raise ValueError("시퀀스 JSON 루트는 list여야 합니다.")
    except Exception as e:
        _push_story_json("SKIP (PARSE_FAIL)")
        _append_anim_history_log(ext, f"[ANIM] JSON 파싱실패 | event={seq} | action={action_text} | file={p.name} | err={e}")
        if verbose:
            print(f"[ANIM MAP] JSON 파싱 실패: {p} err={e}", flush=True)
        return

    # 시뮬레이션 중에는 "빈 JSON([])"을 실행(run)하면 runner 초기화/복원 경로를 타면서
    # 포즈/숨김이 튀는 원인이 된다. 빈 시퀀스는 실행하지 않고 스킵한다.
    if not parsed:
        _push_story_json("SKIP (EMPTY)")
        _append_anim_history_log(
            ext,
            f"[ANIM] 스킵(EMPTY JSON) | t={sim_time or '-'} | event={seq} | action={action_text} | file={p.name}",
        )
        if verbose:
            print(f"[ANIM MAP] 빈 JSON([]) 스킵: {p} event={seq}", flush=True)
        return

    # 예상 총 길이(초): 엑셀/로그에 같이 남길 수 있게 추정
    # 표시/예상 시간은 1배속 기준(콘텐츠 기준)으로 유지한다.
    # (실제 재생 속도는 SequenceRunner.run(speed_scale=...)로 별도 적용)
    est_total = _estimate_sequence_total_duration_sec_for_log(parsed, speed_scale=1.0)
    est_text = f"{est_total:.2f}s" if isinstance(est_total, (float, int)) else "미확인"

    step_types: List[str] = []
    for step in parsed:
        if isinstance(step, dict):
            t = str(step.get("type", "")).strip().upper()
            if t:
                step_types.append(t)
    if step_types:
        preview = ",".join(step_types[:4]) + ("..." if len(step_types) > 4 else "")
    else:
        preview = "EMPTY"

    _append_anim_history_log(
        ext,
        f"[ANIM] 실행준비완료 | t={sim_time or '-'} | event={seq} | est={est_text} | action={action_text} | file={p.name} | steps={len(parsed)}({preview}) | runner={runner} | rule={rule_name or '-'}",
    )
    _push_story_json("PLAN (READY)")
    # 실제 실행 연결: 동시에 여러 run() 호출 시 기존 애니가 끊기는 문제를 막기 위해
    # "시뮬레이션 애니 실행 큐"로 직렬화한다.
    try:
        if not isinstance(getattr(ext, "_sim_anim_pending", None), list):
            ext._sim_anim_pending = []

        def _start_job(job: Dict[str, Any]) -> None:
            # 화면별 runner/active/pending/pause로 분리한다(멀티에서 덮어쓰기/간섭 방지).
            try:
                scr_i = int(str(job.get("tbs_sim_screen", "1") or "1").strip() or "1")
            except Exception:
                scr_i = 1
            scr_i = max(1, scr_i)
            try:
                runners = getattr(ext, "_sim_runners_by_screen", None)
                if not isinstance(runners, dict):
                    runners = {}
                    ext._sim_runners_by_screen = runners
            except Exception:
                runners = {}
                ext._sim_runners_by_screen = runners
            try:
                runner_obj = runners.get(str(scr_i))
            except Exception:
                runner_obj = None
            if runner_obj is None:
                try:
                    from .sequence_engine import SequenceRunner
                    runner_obj = SequenceRunner(
                        registry=getattr(ext, "_tbs_registry", None),
                        scheduler=getattr(ext, "_tbs_scheduler", None),
                        evaluator=getattr(ext, "_tbs_evaluator", None),
                    )
                    runners[str(scr_i)] = runner_obj
                except Exception:
                    runner_obj = getattr(ext, "_sim_runner", None)

            try:
                pause_map = getattr(ext, "_sim_tick_pause_events_by_screen", None)
                if not isinstance(pause_map, dict):
                    pause_map = {}
                    ext._sim_tick_pause_events_by_screen = pause_map
            except Exception:
                pause_map = {}
                ext._sim_tick_pause_events_by_screen = pause_map
            pause_evt = pause_map.get(str(scr_i))
            if pause_evt is None:
                pause_evt = threading.Event()
                pause_map[str(scr_i)] = pause_evt

            started_wall = time.monotonic()
            active = dict(job)
            active["_started_wall"] = started_wall
            try:
                active_by = getattr(ext, "_sim_anim_active_by_screen", None)
                if not isinstance(active_by, dict):
                    active_by = {}
                    ext._sim_anim_active_by_screen = active_by
                active_by[str(scr_i)] = active
            except Exception:
                ext._sim_anim_active = active
            try:
                if isinstance(job.get("est_total"), (float, int)) and float(job.get("est_total")) > 0.0:
                    try:
                        until_by = getattr(ext, "_sim_tick_pause_until_wall_by_screen", None)
                        if not isinstance(until_by, dict):
                            until_by = {}
                            ext._sim_tick_pause_until_wall_by_screen = until_by
                        until_by[str(scr_i)] = float(started_wall) + float(job.get("est_total"))
                    except Exception:
                        ext._sim_tick_pause_until_wall = float(started_wall) + float(job.get("est_total"))
                else:
                    try:
                        until_by = getattr(ext, "_sim_tick_pause_until_wall_by_screen", None)
                        if isinstance(until_by, dict):
                            until_by[str(scr_i)] = None
                    except Exception:
                        ext._sim_tick_pause_until_wall = None
            except Exception:
                try:
                    until_by = getattr(ext, "_sim_tick_pause_until_wall_by_screen", None)
                    if isinstance(until_by, dict):
                        until_by[str(scr_i)] = None
                except Exception:
                    ext._sim_tick_pause_until_wall = None

            def _on_done():
                # 포트상태 점(●) 감소 시점
                # - ARRIVED(OHT->*) 애니가 "포트 도착"을 의미하므로, 완료 후 생성 토큰 1개 소모
                # - REMOVED 애니가 "회수 진행"이므로, 완료 후 회수 토큰 1개 소모
                # (요청으로 제거) 포트상태 좌/우 점 표시 기능 비활성화
                # 정책 변경:
                # - 애니메이션 완료 후에는 "완료된 자세 그대로" 유지한다.
                # - 다음 애니메이션 시작 시점에만 시퀀서 stop() 경로(=baseline 복원/초기화)가 동작하도록,
                #   완료 직후 baseline을 현재 자세로 덮어쓰지 않는다.
                # 화면별 pending 큐에서 다음 job만 이어서 실행
                pending_by = getattr(ext, "_sim_anim_pending_by_screen", None)
                pending = []
                if isinstance(pending_by, dict):
                    pending = pending_by.get(str(scr_i), []) or []
                if isinstance(pending, list) and pending:
                    # 우선순위 큐: _priority 낮은 job 먼저
                    try:
                        pending.sort(key=lambda j: int((j or {}).get("_priority", 10)) if isinstance(j, dict) else 10)
                    except Exception:
                        pass
                    nxt = pending.pop(0)
                    if isinstance(pending_by, dict):
                        pending_by[str(scr_i)] = pending
                    _start_job(nxt)
                    return
                if pause_evt is not None:
                    try:
                        pause_evt.clear()
                    except Exception:
                        pass
                try:
                    until_by = getattr(ext, "_sim_tick_pause_until_wall_by_screen", None)
                    if isinstance(until_by, dict):
                        until_by[str(scr_i)] = None
                except Exception:
                    pass
                try:
                    active_by = getattr(ext, "_sim_anim_active_by_screen", None)
                    if isinstance(active_by, dict):
                        active_by[str(scr_i)] = {}
                except Exception:
                    pass
                try:
                    _refresh_sim_progress_from_last(ext)
                except Exception:
                    pass

            try:
                if runner_obj is not None:
                    runner_obj.on_sequence_completed = _on_done  # type: ignore[attr-defined]
            except Exception:
                pass

            sp = 1.0
            try:
                m = getattr(ext, "_sim_speed_model", None)
                if m is not None:
                    sp = max(0.1, float(m.get_value_as_float()))
            except Exception:
                sp = 1.0
            proc_priority = False
            try:
                ppm = getattr(ext, "_sim_process_time_priority_model", None)
                if ppm is not None:
                    proc_priority = bool(ppm.get_value_as_bool())
            except Exception:
                proc_priority = False

            # 배속>1일 때: 단일 화면에서만 애니·sim tick 동기를 위해 pause 사용.
            # 분할 N>1에서는 한 화면 애니가 다른 화면 엔진 틱까지 멈추어 공정시간·막대가 끊겨 보이므로 적용하지 않는다.
            if (
                (not proc_priority)
                and sp > 1.0
                and pause_evt is not None
                and (not _is_multi_viewport_sim(ext))
            ):
                try:
                    pause_evt.set()
                except Exception:
                    pass
            # JSON 시퀀스 재생 중에도 sim tick이 돌아가야 _wait_with_progress(공정)와 애니가 동시에 진행된다.
            # 배속>1일 때만 pause_evt.set()으로 tick을 잠시 맞춤(1배속에서는 set 하지 않음).
            _ctx_run = _usd_context_name_for_sim_screen(ext, scr_i)
            if runner_obj is not None:
                runner_obj.run(job.get("parsed", []), usd_context_name=_ctx_run, speed_scale=sp)
            try:
                _refresh_sim_progress_from_last(ext)
            except Exception:
                pass

        try:
            _scr = int(str(payload.get("tbs_sim_screen", "1") or "1").strip() or "1")
        except Exception:
            _scr = 1
        _scr = max(1, _scr)
        job = {
            "t": sim_time,
            "event": seq,
            "file": p.name,
            "path": str(p),
            "action": action_text,
            "est": est_text,
            "est_total": float(est_total) if isinstance(est_total, (float, int)) else None,
            "runner": runner,
            "rule": rule_name or "-",
            "lot_id": lot_id,
            "from_port_id": from_port,
            "to_port_id": to_port,
            "port_id": port,
            "parsed": parsed,
            "tbs_sim_screen": str(_scr),
        }
        # 우선순위: 생성(OHT->EP 직접투입 등) / 회수(REMOVED) 는 현재 애니가 끝나자마자 즉시 실행되어야 한다.
        # - 선점(interrupt)은 하지 않고, pending 큐의 "앞"에 삽입한다.
        try:
            is_pickup = str(seq).strip().upper() == str(xml_generator.SEQ_REMOVED).strip().upper()
        except Exception:
            is_pickup = False
        try:
            is_spawn = (
                str(seq).strip().upper() == str(xml_generator.SEQ_ARRIVED).strip().upper()
                and str(from_port).strip().upper() == "OHT"
                and str(to_port).strip().upper().startswith("EP")
            )
        except Exception:
            is_spawn = False
        job["_priority"] = 0 if (is_spawn or is_pickup) else 10
        # 화면별 runner의 busy 여부를 본다.
        runner_busy = False
        try:
            runners = getattr(ext, "_sim_runners_by_screen", None)
            rr = runners.get(str(_scr)) if isinstance(runners, dict) else None
            runner_busy = bool(rr is not None and getattr(rr, "is_running", lambda: False)())
        except Exception:
            runner_busy = False
        if runner_busy:
            try:
                pending_by = getattr(ext, "_sim_anim_pending_by_screen", None)
                if not isinstance(pending_by, dict):
                    pending_by = {}
                    ext._sim_anim_pending_by_screen = pending_by
                pending = pending_by.get(str(_scr), [])
                if not isinstance(pending, list):
                    pending = []
                if int(job.get("_priority", 10)) <= 0:
                    pending.insert(0, job)
                else:
                    pending.append(job)
                pending_by[str(_scr)] = pending
            except Exception:
                pass
            _append_anim_history_log(
                ext,
                f"[ANIM] 대기큐적재 | screen={_scr} | event={seq} | est={est_text} | action={action_text} | file={p.name}",
            )
            try:
                _refresh_sim_progress_from_last(ext)
            except Exception:
                pass
            return
        _start_job(job)
    except Exception as e:
        _append_anim_history_log(ext, f"[ANIM] 실행실패 | event={seq} | action={action_text} | file={p.name} | err={e}")
        pause_evt = getattr(ext, "_sim_tick_pause_event", None)
        if pause_evt is not None:
            try:
                pause_evt.clear()
            except Exception:
                pass
        try:
            ext._sim_tick_pause_until_wall = None
        except Exception:
            pass

    if verbose:
        print(
            f"[ANIM MAP] 이벤트={seq} -> JSON 준비완료: {p} "
            f"(runner={runner}, rule={rule_name or '-'}, lot={payload.get('lot_id','')}, port={payload.get('port_id','')}, "
            f"from={payload.get('from_port_id','')}, to={payload.get('to_port_id','')})",
            flush=True,
        )


def _estimate_anim_duration_for_gate_payload(ext: Any, payload: Dict[str, str]) -> float:
    """
    simulation_engine의 on_gate에서 호출되는 "애니메이션 예상 길이" 계산기.
    - 게이트 시점에 XML 생성/역파싱 → rules/map 매핑 → JSON 파싱 → 총 duration 추정
    - 실패하면 0.0 반환(=애니 대기 없음)
    """
    try:
        seq_raw = str(payload.get("seq", "") or "").strip()
        if not seq_raw:
            return 0.0
        seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)
        fr = str(payload.get("from_port_id", "") or "")
        to = str(payload.get("to_port_id", "") or "")
        port = str(payload.get("port_id", "") or "")

        # 1차: 원본 payload 기준(최소한 map fallback은 항상 시도)
        mapping_payload = dict(payload or {})
        mapping_payload["seq"] = seq

        # 2차: XML 표준화가 가능하면 덮어쓴다(우선 적용)
        try:
            if seq in xml_generator.FROM_TO_SEQS:
                xml_text = xml_generator.build_xml_string(
                    seq,
                    from_port_id=_parse_port_num(fr, 1),
                    to_port_id=_parse_port_num(to, 1),
                )
            elif seq in xml_generator.PORT_ID_ONLY_SEQS:
                xml_text = xml_generator.build_xml_string(seq, port_id=_parse_port_num(port, 1))
            else:
                xml_text = ""
            if xml_text:
                parsed = xml_generator.parse_xml_string(xml_text) or {}
                seq_for_mapping = str(parsed.get("sequence_name", "") or "").strip().upper() or seq
                mapping_payload["seq"] = seq_for_mapping
                mapping_payload["from_port_id"] = _normalize_port_text_from_xml(str(parsed.get("from_port_id", "") or ""), fr)
                mapping_payload["to_port_id"] = _normalize_port_text_from_xml(str(parsed.get("to_port_id", "") or ""), to)
                mapping_payload["port_id"] = _normalize_port_text_from_xml(str(parsed.get("port_id", "") or ""), port)
                seq = seq_for_mapping
        except Exception:
            # XML 표준화 실패해도 원본 payload로 rules/map 추정을 계속 시도한다.
            pass

        # 3) rules/map 매핑
        mapped_json, _meta, _rule, _src = _resolve_event_animation_entry(seq, mapping_payload)
        if not mapped_json:
            return 0.0

        # 4) JSON 파싱 + 총 길이 추정
        pth = _normalize_json_path(mapped_json)
        if not pth.is_file():
            return 0.0
        parsed_steps = json.loads(pth.read_text(encoding="utf-8"))
        if not isinstance(parsed_steps, list):
            return 0.0
        # 게이트 표시/예상 시간은 1배속 기준으로 유지한다(배속은 진행 속도만 변경).
        est = _estimate_sequence_total_duration_sec_for_log(parsed_steps, speed_scale=1.0)
        return max(0.0, float(est)) if isinstance(est, (float, int)) else 0.0
    except Exception:
        return 0.0


def _capture_per_screen_sim_settings(ext: Any) -> Dict[str, Any]:
    """
    멀티 화면별 저장용 스냅샷.

    ``prompt.md`` 기준: LOT 수, 생성/회수 간격, 초기 적재·비가동 포트, 4구간 시간만 포함.
    시뮬 속도·로그 주기·공정시간 우선·각 공정 확인은 전역 공통으로 두고 여기서는 제외한다.
    """
    d: Dict[str, Any] = {}
    try:
        d["ep_count_idx"] = int(ext._sim_ep_count_combo.model.get_item_value_model().as_int)
    except Exception:
        d["ep_count_idx"] = 0
    try:
        d["lot_count"] = max(1, ext._sim_lot_count_model.get_value_as_int())
    except Exception:
        d["lot_count"] = 6
    try:
        d["spawn_min"] = float(ext._sim_lot_spawn_min_model.get_value_as_float())
        d["spawn_max"] = float(ext._sim_lot_spawn_max_model.get_value_as_float())
        d["pue_min"] = float(ext._sim_pickup_evt_min_model.get_value_as_float())
        d["pue_max"] = float(ext._sim_pickup_evt_max_model.get_value_as_float())
    except Exception:
        d["spawn_min"], d["spawn_max"] = 15.0, 40.0
        d["pue_min"], d["pue_max"] = 50.0, 70.0
    for key, attr in (
        ("oht_bp1_min", "_sim_oht_bp1_min_model"),
        ("oht_bp1_max", "_sim_oht_bp1_max_model"),
        ("bp1_bp_min", "_sim_bp1_bp_min_model"),
        ("bp1_bp_max", "_sim_bp1_bp_max_model"),
        ("bp_ep_min", "_sim_bp_ep_min_model"),
        ("bp_ep_max", "_sim_bp_ep_max_model"),
        ("ep_oht_min", "_sim_ep_oht_min_model"),
        ("ep_oht_max", "_sim_ep_oht_max_model"),
    ):
        try:
            m = getattr(ext, attr, None)
            d[key] = float(m.get_value_as_float()) if m is not None else 5.0
        except Exception:
            d[key] = 5.0
    # FOUP 공정 시간(min/max)
    try:
        mnm = getattr(ext, "_sim_foup_proc_min_model", None)
        mxm = getattr(ext, "_sim_foup_proc_max_model", None)
        d["foup_proc_min"] = float(mnm.get_value_as_float()) if mnm is not None else 30.0
        d["foup_proc_max"] = float(mxm.get_value_as_float()) if mxm is not None else 60.0
    except Exception:
        d["foup_proc_min"], d["foup_proc_max"] = 30.0, 60.0
    for key, attr in (
        ("init_inout", "_sim_init_inout_model"),
        ("init_bp1", "_sim_init_bp1_model"),
        ("init_bp2", "_sim_init_bp2_model"),
        ("init_bp3", "_sim_init_bp3_model"),
        ("init_bp4", "_sim_init_bp4_model"),
        ("init_ep1", "_sim_init_ep1_model"),
        ("init_ep2", "_sim_init_ep2_model"),
        ("init_ep3", "_sim_init_ep3_model"),
        ("fault_inout", "_sim_fault_inout_model"),
        ("fault_bp1", "_sim_fault_bp1_model"),
        ("fault_bp2", "_sim_fault_bp2_model"),
        ("fault_bp3", "_sim_fault_bp3_model"),
        ("fault_bp4", "_sim_fault_bp4_model"),
        ("fault_ep1", "_sim_fault_ep1_model"),
        ("fault_ep2", "_sim_fault_ep2_model"),
        ("fault_ep3", "_sim_fault_ep3_model"),
    ):
        try:
            m = getattr(ext, attr, None)
            d[key] = bool(m.get_value_as_bool()) if m is not None else False
        except Exception:
            d[key] = False
    return d


def _refresh_sim_per_screen_status_labels(ext: Any) -> None:
    """
    화면별 시뮼 설정 스냅샷(``_sim_per_screen_snapshots``) 존재 여부를 라벨에 반영한다.

    - 인덱스 i(0-based)는 화면 i+1 과 대응한다.
    - ``_on_save_sim_settings_to_screen`` / 자동 채움 후 호출되어 “(저장됨)/(미저장)” 만 갱신한다.
    """
    labels = getattr(ext, "_sim_per_screen_status_labels", None)
    snaps = getattr(ext, "_sim_per_screen_snapshots", None)
    if not isinstance(labels, list) or not isinstance(snaps, list):
        return
    for i, lbl in enumerate(labels):
        if lbl is None:
            continue
        try:
            ok = i < len(snaps) and snaps[i] is not None
            lbl.text = "(저장됨)" if ok else "(미저장)"
        except Exception:
            pass


def _refresh_sim_per_screen_rows(ext: Any) -> None:
    """분할 수(1~4, 3분할 포함)에 맞춰 화면별 설정 행 표시를 갱신한다."""
    block = getattr(ext, "_sim_per_screen_block", None)
    rows = getattr(ext, "_sim_per_screen_row_hstacks", None)
    if block is None or not isinstance(rows, list):
        return
    try:
        n = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    except Exception:
        n = 1
    try:
        block.visible = n > 1
    except Exception:
        pass
    for i, h in enumerate(rows):
        if h is None:
            continue
        try:
            h.visible = n > 1 and (i + 1) <= n
        except Exception:
            pass
    _refresh_sim_per_screen_status_labels(ext)


def _on_save_sim_settings_to_screen(ext: Any, screen_1based: int) -> None:
    """제어창 현재 값을 해당 화면 인덱스(1~) 스냅샷에 저장한다."""
    if screen_1based < 1 or screen_1based > 4:
        return
    try:
        snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
    except Exception:
        snaps = [None, None, None, None]
    while len(snaps) < 4:
        snaps.append(None)
    snaps = snaps[:4]
    try:
        snaps[screen_1based - 1] = _capture_per_screen_sim_settings(ext)
        ext._sim_per_screen_snapshots = snaps
    except Exception:
        pass
    _refresh_sim_per_screen_status_labels(ext)
    try:
        _append_sim_log(ext, f"[SIM UI] 화면{screen_1based}에 현재 시뮼 설정(LOT·간격·적재/고장·시간) 저장")
    except Exception:
        pass
    try:
        _sync_ep3_port_cell_visibility(ext)
    except Exception:
        pass
    try:
        sim_multi_view.schedule_viewport_snapshot_hud_refresh(ext)
    except Exception:
        pass


def _auto_fill_per_screen_snapshots_on_start(ext: Any) -> None:
    """시작 시: 미저장 화면에는 제어창 현재값을 복사해 둔다(``prompt.md`` 자동 반영)."""
    try:
        n_ch = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    except Exception:
        n_ch = 1
    # 요구사항: 기본화면(화면1) 외에는 "저장"을 눌렀을 때만 스냅샷에 반영되어야 한다.
    # 따라서 start 시점에 미저장(None) 화면을 자동으로 dict로 채워 (저장됨) 상태로 만드는 동작은 하지 않는다.
    # 엔진 생성 시에는 아래 on_sim_start_clicked()가 snaps[i]가 None이면 UI 캡처(cap)를 폴백으로 사용한다.
    _refresh_sim_per_screen_status_labels(ext)
    try:
        sim_multi_view.schedule_viewport_snapshot_hud_refresh(ext)
    except Exception:
        pass


def _sync_default_sim_snapshot_from_ui(ext: Any) -> None:
    """
    제어창(UI 모델) 값을 “기본 스냅샷(dict)”으로 즉시 동기화한다.

    목적:
    - 사용자가 제어창에서 시간/간격/초기포트 등을 바꾸면, 그 값이 "기본값"으로 한 곳에서만 관리되게 한다.
    - 화면별 스냅샷 구조는 유지하되,
      - 화면1 스냅샷은 항상 "기본값"으로 갱신
      - 저장하지 않은 화면(None)만 기본값을 자동으로 따라가게 한다.
    """
    # re-entrancy(모델 set_value가 다시 value_changed를 부르는 경우) 방지
    try:
        if bool(getattr(ext, "_sim_snapshot_sync_guard", False)):
            return
        ext._sim_snapshot_sync_guard = True
    except Exception:
        pass
    try:
        try:
            cap = _capture_per_screen_sim_settings(ext)
        except Exception:
            cap = {}
        try:
            snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
        except Exception:
            snaps = [None, None, None, None]
        while len(snaps) < 4:
            snaps.append(None)
        snaps = snaps[:4]

        # 화면1은 “기본값”으로 항상 갱신
        try:
            snaps[0] = dict(cap)
        except Exception:
            snaps[0] = cap

        # 요구사항: 화면2~4는 "저장"을 누르기 전까지는 스냅샷(None) 상태를 유지해야 한다.
        # 따라서 여기서는 화면1(기본값)만 갱신하고, 다른 화면은 자동 채우지 않는다.

        try:
            ext._sim_per_screen_snapshots = snaps
        except Exception:
            pass
        try:
            _refresh_sim_per_screen_status_labels(ext)
        except Exception:
            pass
        try:
            sim_multi_view.schedule_viewport_snapshot_hud_refresh(ext)
        except Exception:
            pass
    finally:
        try:
            ext._sim_snapshot_sync_guard = False
        except Exception:
            pass


def _fault_ports_from_snapshot(snap: Dict[str, Any], ep_count: int) -> Set[str]:
    """스냅샷의 고장 포트 체크박스를 집합으로 변환한다."""
    out: Set[str] = set()
    pairs = (
        ("INOUT", "fault_inout"),
        ("BP1", "fault_bp1"),
        ("BP2", "fault_bp2"),
        ("BP3", "fault_bp3"),
        ("BP4", "fault_bp4"),
        ("EP1", "fault_ep1"),
        ("EP2", "fault_ep2"),
        ("EP3", "fault_ep3"),
    )
    for port, key in pairs:
        try:
            if bool(snap.get(key)):
                out.add(port)
        except Exception:
            pass
    if ep_count < 3:
        out.discard("EP3")
        out.discard("BP4")
    return out


def _timing_and_init_from_snapshot(ext: Any, snap: Dict[str, Any]) -> Tuple[SimulationTimingConfig, SimulationInitConfig]:
    """화면별 스냅샷으로 채널 전용 ``SimulationTimingConfig`` / ``SimulationInitConfig`` 를 만든다."""
    try:
        ep_count_idx = int(snap.get("ep_count_idx", 0) or 0)
    except Exception:
        ep_count_idx = 0
    ep_count = 2 if ep_count_idx == 0 else 3
    initial_full_ports: List[str] = []
    if bool(snap.get("init_inout")):
        initial_full_ports.append("INOUT")
    if bool(snap.get("init_bp1")):
        initial_full_ports.append("BP1")
    if bool(snap.get("init_bp2")):
        initial_full_ports.append("BP2")
    if bool(snap.get("init_bp3")):
        initial_full_ports.append("BP3")
    if ep_count >= 3 and bool(snap.get("init_bp4")):
        initial_full_ports.append("BP4")
    if bool(snap.get("init_ep1")):
        initial_full_ports.append("EP1")
    if bool(snap.get("init_ep2")):
        initial_full_ports.append("EP2")
    if ep_count >= 3 and bool(snap.get("init_ep3")):
        initial_full_ports.append("EP3")
    try:
        spawn_imin = max(0.1, float(snap.get("spawn_min", 15.0)))
        spawn_imax = max(0.1, float(snap.get("spawn_max", 40.0)))
    except Exception:
        spawn_imin, spawn_imax = 15.0, 40.0
    if spawn_imin > spawn_imax:
        spawn_imin, spawn_imax = spawn_imax, spawn_imin
    try:
        pue_min = max(0.1, float(snap.get("pue_min", 50.0)))
        pue_max = max(0.1, float(snap.get("pue_max", 70.0)))
    except Exception:
        pue_min, pue_max = 50.0, 70.0
    if pue_min > pue_max:
        pue_min, pue_max = pue_max, pue_min

    def _f_snap(key: str, default: float = 5.0) -> float:
        try:
            return max(0.1, float(snap.get(key, default)))
        except Exception:
            return default

    timing = SimulationTimingConfig(
        oht_to_bp1_min=_f_snap("oht_bp1_min"),
        oht_to_bp1_max=_f_snap("oht_bp1_max"),
        bp1_to_bp_min=_f_snap("bp1_bp_min"),
        bp1_to_bp_max=_f_snap("bp1_bp_max"),
        bp_to_ep_min=_f_snap("bp_ep_min"),
        bp_to_ep_max=_f_snap("bp_ep_max"),
        ep_to_oht_min=_f_snap("ep_oht_min"),
        ep_to_oht_max=_f_snap("ep_oht_max"),
        lot_spawn_interval_min=spawn_imin,
        lot_spawn_interval_max=spawn_imax,
        pickup_event_interval_min=pue_min,
        pickup_event_interval_max=pue_max,
        foup_process_min=_f_snap("foup_proc_min", 30.0),
        foup_process_max=_f_snap("foup_proc_max", 60.0),
    )
    try:
        lot_count = max(1, int(snap.get("lot_count", 6) or 6))
    except Exception:
        lot_count = 6
    proc_pri = False
    try:
        ppm = getattr(ext, "_sim_process_time_priority_model", None)
        if ppm is not None:
            proc_pri = bool(ppm.get_value_as_bool())
    except Exception:
        proc_pri = False
    init = SimulationInitConfig(
        ep_count=ep_count,
        initial_full_ports=initial_full_ports,
        max_oht_lots=lot_count,
        process_time_priority=proc_pri,
    )
    return timing, init


def _sync_sim_multi_split_row_visibility(ext: Any) -> None:
    """
    제어창의 **시뮼 뷰포트 분할** 설정 행(``_sim_multi_split_row``) 표시 여부를 갱신한다.

    - USD 파일 스테이지가 로드되고(``is_usd_file_stage_loaded``)·내부 플래그 ``_tbs_multi_split_usd_ready`` 가 참일 때만 표시.
    - 조건 미충족 시 ``_force_sim_split_to_default`` 로 분할 UI·카운트를 1로 되돌린다.
    """
    row = getattr(ext, "_sim_multi_split_row", None)
    if row is None:
        return
    try:
        if get_stage() is None:
            try:
                ext._tbs_multi_split_usd_ready = False
            except Exception:
                pass
    except Exception:
        pass
    try:
        st_ok = bool(is_usd_file_stage_loaded())
        ready = bool(getattr(ext, "_tbs_multi_split_usd_ready", False))
        row.visible = bool(ready and st_ok)
    except Exception:
        try:
            row.visible = False
        except Exception:
            pass
    if not getattr(row, "visible", False):
        _force_sim_split_to_default(ext)


def _sync_sim_split_checkboxes_from_ext_count(ext: Any) -> None:
    """``ext._sim_viewport_split_count``(실제 적용 분할 수)에 맞춰 1~4 체크박스를 맞춘다. ``apply`` 를 호출하지 않는다."""
    if getattr(ext, "_sim_split_mutate_guard", False):
        return
    try:
        n = int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
    except Exception:
        n = 1
    n = max(1, min(4, n))
    models = getattr(ext, "_sim_split_cb_models", None)
    if not isinstance(models, list) or len(models) != 4:
        return
    ext._sim_split_mutate_guard = True
    try:
        for i, m in enumerate(models, start=1):
            try:
                m.set_value(i == n)
            except Exception:
                pass
    finally:
        ext._sim_split_mutate_guard = False
    try:
        _refresh_sim_per_screen_rows(ext)
    except Exception:
        pass


def _force_sim_split_to_default(ext: Any) -> None:
    """분할 UI를 1개 시뮼만 선택된 상태로 되돌린다."""
    ext._sim_split_mutate_guard = True
    try:
        models = getattr(ext, "_sim_split_cb_models", None)
        if isinstance(models, list) and len(models) == 4:
            for i, m in enumerate(models, start=1):
                try:
                    m.set_value(i == 1)
                except Exception:
                    pass
        try:
            ext._sim_viewport_split_count = 1
        except Exception:
            pass
    finally:
        ext._sim_split_mutate_guard = False
    try:
        ext._sim_per_screen_snapshots = [None, None, None, None]
    except Exception:
        pass
    try:
        sim_multi_view.apply_sim_viewport_split_layout(ext, 1)
    except Exception:
        pass
    try:
        _refresh_sim_per_screen_rows(ext)
    except Exception:
        pass


def _on_sim_split_choice_changed(ext: Any, idx: int, m: Any) -> None:
    """1~4 상호 배타 체크 + 분할 스텁 적용."""
    if getattr(ext, "_sim_split_mutate_guard", False):
        return
    try:
        if not m.get_value_as_bool():
            cur = int(getattr(ext, "_sim_viewport_split_count", 1) or 1)
            if cur == idx:
                ext._sim_split_mutate_guard = True
                try:
                    m.set_value(True)
                finally:
                    ext._sim_split_mutate_guard = False
            return
        ext._sim_split_mutate_guard = True
        try:
            models = list(getattr(ext, "_sim_split_cb_models", []) or [])
            for j, md in enumerate(models, start=1):
                if j != idx:
                    try:
                        if md.get_value_as_bool():
                            md.set_value(False)
                    except Exception:
                        pass
        finally:
            ext._sim_split_mutate_guard = False
        sim_multi_view.apply_sim_viewport_split_layout(ext, idx)
    except Exception as err:
        try:
            print(f"[TBS multi-sim] split UI err: {err}", flush=True)
        except Exception:
            pass


def _usd_context_name_for_sim_screen(ext: Any, screen: int) -> Optional[str]:
    """
    시뮼 **화면 인덱스**에 대응하는 USD 컨텍스트 이름을 반환한다.

    - 화면 1: ``None`` → 기본 ``omni.usd`` 컨텍스트.
    - 화면 2 이상: ``sim_multi_view`` 가 실제 생성한 이름(``ext._sim_multi_context_names``) 우선,
      없으면 ``morph_tbs_split_aux_{screen-1}`` 폴백.
    """
    try:
        s = int(screen)
    except Exception:
        s = 1
    if s <= 1:
        return None
    try:
        names = list(getattr(ext, "_sim_multi_context_names", []) or [])
    except Exception:
        names = []
    idx = s - 2
    if 0 <= idx < len(names):
        nm = str(names[idx] or "").strip()
        return nm if nm else None
    return f"morph_tbs_split_aux_{s - 1}"


def _sim_monitor_channel_count(ext: Any) -> int:
    """
    시뮼 모니터(포트상태·진행현황·SIM 로그) 열 개수를 반환한다.

    ``ext._sim_viewport_split_count``(1~4)와 동일하며, ``_rebuild_sim_monitor_split_ui`` 의 열 수와 맞춘다.
    """
    return max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))


def _snapshot_monitor_channel_texts(ext: Any) -> Tuple[Dict[int, str], Dict[int, str]]:
    """
    ``_sim_monitor_channels`` 에서 화면별 진행/이력 라벨 텍스트를 읽어 재빌드 시 복원한다.

    Returns:
        (history_by_screen, progress_by_screen) — 키는 1-based 화면 인덱스.
    """
    saved_h: Dict[int, str] = {}
    saved_p: Dict[int, str] = {}
    old = getattr(ext, "_sim_monitor_channels", None)
    if not isinstance(old, list):
        return saved_h, saved_p
    for ch in old:
        if not isinstance(ch, dict):
            continue
        try:
            si = int(ch.get("screen", 0))
        except Exception:
            continue
        if si <= 0:
            continue
        hl = ch.get("history_label")
        pl = ch.get("progress_label")
        if hl is not None:
            try:
                saved_h[si] = str(hl.text or "")
            except Exception:
                pass
        if pl is not None:
            try:
                saved_p[si] = str(pl.text or "")
            except Exception:
                pass
    return saved_h, saved_p


def _ep_occ_timeline_layout_dims(ext: Any) -> Tuple[int, int, int, int, int]:
    """
    포트 아래 EP 타임라인(막대) Kit 가로 레이아웃.
    뷰포트 분할 시 열당 폭이 매우 좁아져, 막대·라벨·패딩을 함께 줄인다.

    Returns:
        (bar_w, name_w, val_w, frame_pad, row_sp) — 우측 누적 초 라벨 폭=val_w.
    """
    try:
        nsp = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    except Exception:
        nsp = 1
    if nsp <= 1:
        return (420, 64, 56, 6, 6)
    if nsp == 2:
        # 약 ~270px 행 폭 목표(이름+막대+초+간격+프레임 여유)
        return (168, 48, 44, 3, 4)
    if nsp == 3:
        return (120, 44, 40, 2, 3)
    return (88, 40, 36, 2, 2)


def _ep_timeline_host_horizontal_scroll_policy(ext: Any) -> Any:
    """분할 시 막대 행이 열보다 넓으면 가로 스크롤로 잘림을 피한다."""
    try:
        nsp = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    except Exception:
        nsp = 1
    if nsp <= 1:
        return ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF
    for name in ("SCROLLBAR_AS_NEEDED", "SCROLLBAR_AUTO", "SCROLLBAR_ALWAYS_ON"):
        pol = getattr(ui.ScrollBarPolicy, name, None)
        if pol is not None:
            return pol
    return ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF


def _create_sim_monitor_channel_column(ext: Any, screen: int) -> Dict[str, Any]:
    """
    단일 화면(채널)용 모니터 UI 블록을 만든다.

    - 구성: 포트상태 스크롤 → 진행현황 스크롤 → SIM 이력 스크롤.
    - 반환 dict 는 ``screen``, ``port_frame``/``port_cells``, ``progress_label``, ``history_label`` 등
      ``_rebuild_sim_monitor_split_ui``·``_update_sim_progress``·``_append_sim_log_channel`` 가 참조한다.
    """
    ch: Dict[str, Any] = {"screen": int(screen)}
    ch["port_cells"] = {}
    ch["port_cell_boxes"] = {}
    ch["port_frame"] = ui.ScrollingFrame(height=112, style={"background_color": 0xFF1A1E26, "border_width": 1, "border_color": 0xFF3A3A3A})
    with ch["port_frame"]:
        with ui.VStack(spacing=4):
            ch["port_header"] = ui.Label(
                f"[포트상태·화면{screen}] 대기 중", height=20, style={"color": 0xFFBFE7FF}
            )
            with ui.VStack(spacing=4):
                with ui.HStack(spacing=4, height=24):
                    with ui.ZStack(width=90, height=24):
                        ch["port_cell_boxes"]["BP1"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["BP1"] = ui.Label("BP1:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                    with ui.ZStack(width=90, height=24):
                        ch["port_cell_boxes"]["BP2"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["BP2"] = ui.Label("BP2:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                    with ui.ZStack(width=90, height=24):
                        ch["port_cell_boxes"]["BP3"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["BP3"] = ui.Label("BP3:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                    ch["port_bp4_cell_container"] = ui.ZStack(width=90, height=24)
                    with ch["port_bp4_cell_container"]:
                        ch["port_cell_boxes"]["BP4"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["BP4"] = ui.Label("BP4:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                with ui.HStack(spacing=4, height=24):
                    with ui.ZStack(width=90, height=24):
                        ch["port_cell_boxes"]["INOUT"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["INOUT"] = ui.Label("IN/OUT:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                    with ui.ZStack(width=90, height=24):
                        ch["port_cell_boxes"]["EP1"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["EP1"] = ui.Label("EP1:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                    with ui.ZStack(width=90, height=24):
                        ch["port_cell_boxes"]["EP2"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_cells"]["EP2"] = ui.Label("EP2:-", width=90, height=24, style={"color": 0xFFFFFFFF})
                    ch["port_ep3_cell_container"] = ui.ZStack(width=90, height=24)
                    with ch["port_ep3_cell_container"]:
                        ch["port_cell_boxes"]["EP3"] = ui.Rectangle(
                            style={"background_color": 0xFF2A2F38, "border_color": 0xFF7B8799, "border_width": 1}
                        )
                        ch["port_ep3_cell"] = ui.Label("EP3:-", width=90, height=24, style={"color": 0xFFFFFFFF})
            pass
    # EP 타임라인 전용 영역(포트상태 바로 아래, 스크롤 밖 고정)
    # - 진행현황/이력과 독립적으로 항상 보이게 별도 영역으로 둔다.
    # NOTE: ui.Frame(height=..) 가 일부 Kit 버전에서 레이아웃에 의해 축소/클리핑되는 사례가 있어,
    # 고정 높이가 확실한 ScrollingFrame을 사용한다(스크롤바는 숨김).
    ch["ep_timeline_host"] = ui.ScrollingFrame(
        height=130,
        horizontal_scrollbar_policy=_ep_timeline_host_horizontal_scroll_policy(ext),
        vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
        style={"background_color": 0x221A1E26, "border_width": 1, "border_color": 0xFF3A3A3A},
    )
    with ch["ep_timeline_host"]:
        ui.Label("", height=1)  # placeholder to stabilize layout
    ch["ep_timeline_widget"] = None
    # 진행현황: 텍스트 + EP 점유 타임라인(막대그래프)
    # 진행현황은 "로그 텍스트" 중심으로 유지. (그래프는 포트상태 아래 전용 영역으로 분리됨)
    # 요구사항: FOUP 공정 진행 표시는 EP 포트별로 "줄을 다르게 고정"하여 깜빡임 없이 표시한다.
    # EP1/EP2/EP3 각 22px 라벨 3줄(총 ~66px) + 기존 텍스트 영역을 함께 두기 위해
    # progress_frame 의 높이를 200 → 270 으로 늘려 자리만 확보한다(레이아웃 안정성).
    ch["progress_frame"] = ui.ScrollingFrame(height=270, style={"background_color": 0xFF1A1E26, "border_width": 1, "border_color": 0xFF3A3A3A})
    with ch["progress_frame"]:
        with ui.VStack(spacing=4):
            # FOUP 공정 진행 라벨(EP 포트별 고정 줄):
            # - 항상 EP1/EP2/EP3 라벨 3개를 만들어 두고, 각 EP 가 자기 줄만 갱신한다.
            # - 진행 중이 아닐 때는 "대기"(회색), 진행 중에는 노란색 텍스트로 구분.
            # - EP 카운트가 1/2 인 경우에도 라벨은 그대로 두고 "대기"로만 표시(레이아웃 흔들림 방지).
            ch["foup_progress_labels"] = {}
            for ep_id in ("EP1", "EP2", "EP3"):
                idle_text = (
                    f"{ep_id} FOUP 공정: 대기"
                    if screen == 1
                    else f"{ep_id} FOUP 공정(화면{screen}): 대기"
                )
                lbl = ui.Label(
                    idle_text,
                    word_wrap=False,
                    height=22,
                    style={"color": 0xFF888888},
                )
                ch["foup_progress_labels"][ep_id] = lbl
            # 호환용(스냅샷/복원/구버전 참조 안전망): 첫 번째 EP 라벨을 단일 슬롯에 매핑해 둔다.
            # _update_sim_progress 의 FOUP 분기는 dict 우선으로 동작한다.
            ch["foup_progress_label"] = ch["foup_progress_labels"]["EP1"]
            ch["progress_label"] = ui.Label("", word_wrap=True, height=170, style={"color": 0xFFFFFFFF})
            # 진행현황 영역에서는 그래프를 표시하지 않는다(포트상태 아래 전용 영역으로 분리).
            ch["progress_ep_timeline_host"] = None
            ch["progress_ep_timeline_widget"] = None
    ch["history_frame"] = ui.ScrollingFrame(height=118, style={"background_color": 0xFF1A1E26, "border_width": 1, "border_color": 0xFF3A3A3A})
    with ch["history_frame"]:
        ch["history_label"] = ui.Label("", word_wrap=True, height=114, style={"color": 0xFFFFFFFF})
    ch["history_label"].text = "[SIM] 대기 중" if screen == 1 else f"[SIM·화면{screen}] 대기 중"
    ch["progress_label"].text = "[진행현황] 없음" if screen == 1 else f"[진행현황·화면{screen}] 없음"
    return ch


def _rebuild_sim_monitor_split_ui(ext: Any) -> None:
    """
    뷰포트 분할 수(1~4)에 맞춰 제어창 하단 시뮼 모니터 영역을 다시 그린다.

    - 기존 ``_sim_monitor_split_inner`` 를 destroy 한 뒤 ``_sim_monitor_channel_count`` 만큼
      ``_create_sim_monitor_channel_column`` 을 HStack/VStack 으로 배치한다.
    - 분할 전 각 채널의 진행/이력 문자열은 ``_snapshot_monitor_channel_texts`` 로 백업 후 복원한다.
    - 단일 채널일 때는 ``ext._sim_progress_label`` 등 레거시 단일 위젯 참조를 [0] 채널에 다시 연결한다.
    """
    host = getattr(ext, "_sim_monitor_split_host", None)
    if host is None:
        return
    saved_h, saved_p = _snapshot_monitor_channel_texts(ext)
    inn = getattr(ext, "_sim_monitor_split_inner", None)
    if inn is not None:
        try:
            inn.destroy()
        except Exception:
            pass
        try:
            ext._sim_monitor_split_inner = None
        except Exception:
            pass
    n = _sim_monitor_channel_count(ext)
    channels: List[Dict[str, Any]] = []
    with host:
        ext._sim_monitor_split_inner = ui.VStack(spacing=6)
    inner = getattr(ext, "_sim_monitor_split_inner", None)
    if inner is None:
        return
    with inner:
        if n == 1:
            channels.append(_create_sim_monitor_channel_column(ext, 1))
        elif n == 2:
            with ui.HStack(spacing=6):
                # width=0 은 HStack 자식에서 가로 공간을 못 받아 패널이 전부 0폭으로 사라질 수 있음 → 균등 분할
                with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                    channels.append(_create_sim_monitor_channel_column(ext, 1))
                with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                    channels.append(_create_sim_monitor_channel_column(ext, 2))
        elif n == 3:
            with ui.VStack(spacing=6):
                with ui.VStack(spacing=4):
                    channels.append(_create_sim_monitor_channel_column(ext, 1))
                with ui.HStack(spacing=6):
                    with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                        channels.append(_create_sim_monitor_channel_column(ext, 2))
                    with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                        channels.append(_create_sim_monitor_channel_column(ext, 3))
        else:
            with ui.VStack(spacing=6):
                with ui.HStack(spacing=6):
                    with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                        channels.append(_create_sim_monitor_channel_column(ext, 1))
                    with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                        channels.append(_create_sim_monitor_channel_column(ext, 2))
                with ui.HStack(spacing=6):
                    with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                        channels.append(_create_sim_monitor_channel_column(ext, 3))
                    with ui.VStack(spacing=4, width=ui.Fraction(1.0)):
                        channels.append(_create_sim_monitor_channel_column(ext, 4))

    for ch in channels:
        if not isinstance(ch, dict):
            continue
        try:
            si = int(ch.get("screen", 0))
        except Exception:
            continue
        if si in saved_h and str(saved_h.get(si) or "").strip():
            try:
                ch["history_label"].text = saved_h[si]
            except Exception:
                pass
        if si in saved_p and str(saved_p.get(si) or "").strip():
            try:
                ch["progress_label"].text = saved_p[si]
            except Exception:
                pass

    try:
        ext._sim_monitor_channels = channels
        ext._sim_monitor_layout_n = n
    except Exception:
        pass

    # 안정성(포트상태 깜빡임/초기화 방지):
    # 분할 레이아웃이 어떤 이유로 재조립되면(port cells가 기본값 "-"로 재생성),
    # 직후 포트상태가 "비었다가 채워졌다"처럼 보이는 깜빡임이 발생할 수 있다.
    # 마지막 점유 스냅샷이 있으면 즉시 복원해 화면을 안정화한다.
    try:
        by_occ = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
        if isinstance(by_occ, dict) and isinstance(channels, list):
            for ch in channels:
                if not isinstance(ch, dict):
                    continue
                try:
                    si = int(ch.get("screen", 1) or 1)
                except Exception:
                    si = 1
                occ = by_occ.get(str(si))
                if isinstance(occ, dict) and occ:
                    try:
                        _update_port_occupancy_panel(ext, occ, sim_time="", screen=si)
                    except Exception:
                        pass
    except Exception:
        pass

    if n == 1 and len(channels) == 1:
        c0 = channels[0]
        try:
            ext._sim_port_state_frame = c0["port_frame"]
            ext._sim_progress_frame = c0["progress_frame"]
            ext._sim_history_frame = c0["history_frame"]
            ext._sim_port_state_header_label = c0["port_header"]
            ext._sim_port_cells = c0["port_cells"]
            ext._sim_port_cell_boxes = c0["port_cell_boxes"]
            ext._sim_port_bp4_cell_container = c0.get("port_bp4_cell_container")
            ext._sim_port_ep3_cell_container = c0.get("port_ep3_cell_container")
            ext._sim_port_ep3_cell = c0.get("port_ep3_cell")
            ext._sim_progress_label = c0["progress_label"]
            ext._sim_history_label = c0["history_label"]
            if getattr(ext, "_sim_history_text", None) is not None:
                ext._sim_history_text.set_value(c0["history_label"].text)
            if getattr(ext, "_sim_progress_text", None) is not None:
                ext._sim_progress_text.set_value(c0["progress_label"].text)
        except Exception:
            pass
    else:
        try:
            ext._sim_port_state_frame = channels[0]["port_frame"]
            ext._sim_progress_frame = channels[0]["progress_frame"]
            ext._sim_history_frame = channels[0]["history_frame"]
            ext._sim_port_state_header_label = None
            ext._sim_port_cells = {}
            ext._sim_port_cell_boxes = {}
            ext._sim_port_bp4_cell_container = None
            ext._sim_port_ep3_cell_container = None
            ext._sim_port_ep3_cell = None
            ext._sim_progress_label = None
            ext._sim_history_label = None
        except Exception:
            pass

    try:
        _sync_ep3_port_cell_visibility(ext)
    except Exception:
        pass


def build_control_window(ext: Any) -> None:
    """TBS 제어창을 만들고 ext에 위젯/모델 참조를 저장."""
    # destroy()가 실패하거나(Kit 이벤트/프레임 타이밍), 핫리로드로 ext 인스턴스가 바뀌면
    # 이전 창이 화면에 남은 채로 새 창이 생성되어 UI가 겹쳐 보일 수 있다.
    # 1) ext 참조 기준 중복 생성 방지
    if getattr(ext, "_control_window", None) is not None:
        return
    # 2) 워크스페이스에 남아있는 동명 창이 있으면 선제 제거(핫리로드/비정상 destroy 대비)
    try:
        ws = getattr(ui, "Workspace", None)
        if ws is not None and hasattr(ws, "get_window"):
            old = ws.get_window("TBS 제어창")
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    try:
                        old.visible = False
                    except Exception:
                        pass
    except Exception:
        pass

    ext._xml_from_port_model = ui.SimpleIntModel(1)
    ext._xml_to_port_model = ui.SimpleIntModel(6)
    ext._xml_port_id_model = ui.SimpleIntModel(1)
    ext._last_generated_xml = ""
    ext._priority_prefix_model = ui.SimpleStringModel(DEFAULT_PRIORITY_NAME_PREFIX)
    ext._sim_lot_count_model = ui.SimpleIntModel(6)
    ext._sim_lot_spawn_min_model = ui.SimpleFloatModel(15.0)
    ext._sim_lot_spawn_max_model = ui.SimpleFloatModel(40.0)
    ext._sim_pickup_evt_min_model = ui.SimpleFloatModel(50.0)
    ext._sim_pickup_evt_max_model = ui.SimpleFloatModel(70.0)
    ext._sim_speed_model = ui.SimpleFloatModel(1.0)
    # 로그 주기 기본값: 1초 고정(요구사항)
    ext._sim_log_interval_model = ui.SimpleFloatModel(1.0)
    ext._sim_confirm_each_step_model = ui.SimpleBoolModel(False)
    # 공정설정 시간 우선(기본 OFF)
    ext._sim_process_time_priority_model = ui.SimpleBoolModel(False)
    ext._sim_oht_bp1_min_model = ui.SimpleFloatModel(5.0)
    ext._sim_oht_bp1_max_model = ui.SimpleFloatModel(10.0)
    ext._sim_bp1_bp_min_model = ui.SimpleFloatModel(5.0)
    ext._sim_bp1_bp_max_model = ui.SimpleFloatModel(10.0)
    ext._sim_bp_ep_min_model = ui.SimpleFloatModel(5.0)
    ext._sim_bp_ep_max_model = ui.SimpleFloatModel(10.0)
    ext._sim_ep_oht_min_model = ui.SimpleFloatModel(5.0)
    ext._sim_ep_oht_max_model = ui.SimpleFloatModel(10.0)
    # FOUP 공정 시간(EP 상) — min/max 랜덤 범위
    ext._sim_foup_proc_min_model = ui.SimpleFloatModel(30.0)
    ext._sim_foup_proc_max_model = ui.SimpleFloatModel(60.0)
    ext._sim_ep_count_combo = None
    # 초기 적재 포트: IN/OUT + BP1~BP4 + EP1~EP3
    # (EP 개수=2이면 BP4/EP3은 UI에서 숨기며 값도 강제로 False로 유지)
    ext._sim_init_inout_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp1_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp2_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp3_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp4_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep1_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep2_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep3_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp4_row = None
    ext._sim_init_ep3_row = None
    # 고장(비가동) 포트 체크박스 모델(런타임 변경 가능)
    ext._sim_fault_inout_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp1_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp2_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp3_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp4_model = ui.SimpleBoolModel(False)
    ext._sim_fault_ep1_model = ui.SimpleBoolModel(False)
    ext._sim_fault_ep2_model = ui.SimpleBoolModel(False)
    ext._sim_fault_ep3_model = ui.SimpleBoolModel(False)
    ext._sim_fault_bp4_row = None
    ext._sim_fault_ep3_row = None
    ext._sim_log_text = ui.SimpleStringModel("[SIM] 대기 중")
    ext._sim_history_text = ui.SimpleStringModel("[SIM] 대기 중")
    ext._sim_progress_text = ui.SimpleStringModel("[진행현황] 없음")
    ext._sim_port_state_text = ui.SimpleStringModel("[포트상태] 대기 중")
    # 요약(애니 실행이력 창): "스토리 1개 + 그 이후 연결된 JSON 목록" 블록을 유지
    # block: {"story": str, "anims": List[str]}
    ext._sim_recent_story_blocks = []
    # (요청으로 제거) 생성/회수 대기 토큰 표시 기능 비활성화
    # (요청으로 제거) 포트상태 좌/우 점 표시 기능은 비활성화
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}
    # 진행현황 RUNNING 라인 디듀프: percent/elapsed/total이 같으면 UI 갱신 스킵
    ext._sim_progress_last_key = {}
    ext._sim_engine = None
    ext._sim_engines = []
    ext._sim_viewport_split_count = 1
    ext._sync_sim_multi_split_ui_fn = None
    ext._tbs_multi_split_usd_ready = False
    ext._tbs_last_loaded_usd_path = ""
    ext._sim_multi_view_apply_token = 0
    ext._sim_multi_viewport_entries = []
    ext._tbs_sim_snapshot_hud_windows = []
    ext._tbs_sim_snapshot_hud_roots = {}
    ext._tbs_split_main_viewport_window = None
    ext._tbs_sim_hud_screen1_label = None
    ext._tbs_sim_hud_screen1_live_sub = None
    ext._tbs_sim_hud_live_ctr = 0
    ext._capture_sim_settings_dict_for_hud_fn = lambda: _capture_per_screen_sim_settings(ext)
    ext._sim_multi_context_names = []
    ext._tbs_split_session_layer_paths = []
    ext._tbs_mdl_https_texture_hint_done = False
    ext._sim_split_mutate_guard = False
    ext._sim_split_cb_models = []
    ext._sim_split_stage_sub = None
    ext._sim_multi_split_row = None
    ext._sim_per_screen_snapshots = [None, None, None, None]
    ext._sim_snapshot_sync_guard = False
    ext._sim_per_screen_block = None
    ext._sim_per_screen_row_hstacks = []
    ext._sim_per_screen_status_labels = []
    ext._sync_sim_per_screen_rows_fn = None
    ext._sim_update_sub = None
    ext._sim_thread = None
    ext._sim_tick_threads = []
    ext._sim_thread_stop = None
    ext._sim_log_queue = None
    ext._sim_log_ui_sub = None
    ext._sim_log_view_combo = None
    ext._sim_progress_frame = None
    ext._sim_history_frame = None
    ext._sim_anim_history_frame = None
    ext._sim_monitor_split_host = None
    ext._sim_monitor_split_inner = None
    ext._sim_monitor_channels = []
    ext._sim_monitor_layout_n = 1
    ext._rebuild_sim_monitor_split_ui_fn = None
    ext._sim_port_state_frame = None
    ext._sim_port_state_header_label = None
    ext._sim_port_cells = {}
    ext._sim_port_cell_boxes = {}
    ext._sim_port_bp4_cell_container = None
    ext._sim_port_ep3_cell = None
    ext._sim_port_ep3_cell_container = None
    ext._sim_progress_label = None
    ext._sim_history_label = None
    ext._sim_anim_history_label = None
    ext._sim_port_state_label = None
    ext._sim_runner = SequenceRunner(
        registry=getattr(ext, "_tbs_registry", None),
        scheduler=getattr(ext, "_tbs_scheduler", None),
        evaluator=getattr(ext, "_tbs_evaluator", None),
    )
    ext._sim_anim_active = {}
    ext._sim_anim_pending = []
    # 애니메이션 재생 중 sim tick을 잠시 멈추기 위한 플래그
    ext._sim_tick_pause_event = threading.Event()
    # 이벤트 확인창(공정확인) 표시 중 sim tick을 잠시 멈추기 위한 플래그
    ext._sim_gate_pause_event = threading.Event()
    # fail-safe: 예상 애니 길이만큼은 최소 pause 유지 (monotonic timestamp)
    ext._sim_tick_pause_until_wall = None
    ext._sim_gate_dialog = None

    ext._control_window = ui.Window("EBS 제어창", width=800, height=840)
    with ext._control_window.frame:
        with ui.ScrollingFrame(
            height=ui.Fraction(1.0),
            style={"ScrollingFrame": {"padding": 4, "margin": 0}},
        ):
            with ui.VStack(spacing=0):
                ext._kit_chrome_hide_model = ui.SimpleBoolModel(KIT_CHROME_HIDE_DEFAULT_ON_LAUNCH)

                def _on_kit_chrome_toggle(model):
                    try:
                        apply_kit_chrome_hidden(ext, bool(model.as_bool))
                    except Exception:
                        pass

                ext._kit_chrome_hide_model.add_value_changed_fn(_on_kit_chrome_toggle)

                with ui.Frame(style={"background_color": 0xFF23262B}):
                    with ui.VStack(padding=8, spacing=8):
                        ui.Label("화면", height=24, style={"color": 0xFFDDDDDD})
                        with ui.HStack(spacing=8, height=28):
                            ui.Label(
                                "기본 메뉴·패널 숨기기 (3D 뷰·TBS·시퀀스 편집기 유지)",
                                width=0,
                                style={"color": 0xFFCCCCCC},
                            )
                            ui.CheckBox(
                                model=ext._kit_chrome_hide_model,
                                width=28,
                                style=CHECKBOX_WHITE_STYLE,
                            )
                ui.Spacer(height=6)
                # USD Load → 별도 창 ``TbsUsdWindow`` (extension.py)
                # with ui.Frame(style={"background_color": 0xFF23262B}):
                #     # 콤보에 과도한 width 지정 시 Kit에서 다음 구역과 겹침이 발생할 수 있어 세로 스택만 사용
                #     with ui.VStack(padding=8, spacing=8):
                #         ui.Label("XML 제너레이터 생성기", height=24, style={"color": 0xFFDDDDDD})
                #         ext._xml_seq_combo = ui.ComboBox(
                #             0,
                #             xml_generator.SEQ_READYTOLOAD,
                #             xml_generator.SEQ_ARRIVED,
                #             xml_generator.SEQ_MOVE_TRANSFERING,
                #             xml_generator.SEQ_MOVE,
                #             xml_generator.SEQ_MOVE_REQ,
                #             xml_generator.SEQ_READYTOUNLOAD,
                #             xml_generator.SEQ_REMOVED,
                #         )
                #         ext._xml_seq_combo.model.add_item_changed_fn(lambda m, *a: on_xml_seq_changed(ext))
                #         with ui.HStack(spacing=8, height=28):
                #             ui.Button("OK", width=72, height=28, clicked_fn=lambda: on_xml_ok_clicked(ext))
                #             ui.Button("제너레이터 실행(역파싱)", height=28, clicked_fn=lambda: on_xml_run_clicked(ext))
                #         ext._xml_ab_inputs_frame = ui.HStack(spacing=8, height=28)
                #         with ext._xml_ab_inputs_frame:
                #             ui.Label("FROM_PORT_ID", width=110, height=28)
                #             ui.IntField(model=ext._xml_from_port_model, width=60, height=28)
                #             ui.Label("TO_PORT_ID", width=90, height=28)
                #             ui.IntField(model=ext._xml_to_port_model, width=60, height=28)
                #         ext._xml_ab_inputs_frame.visible = True

                #         ext._xml_port_inputs_frame = ui.HStack(spacing=8, height=28)
                #         with ext._xml_port_inputs_frame:
                #             ui.Label("PORT_ID", width=110, height=28)
                #             ui.IntField(model=ext._xml_port_id_model, width=60, height=28)
                #         ext._xml_port_inputs_frame.visible = False
                #         ui.Label(
                #             "포트 ID 표: EP1~3=1~3, IN/OUT=5, BP1~4=6~9, OHT=10",
                #             height=18,
                #             style={"color": 0xFF9AA4B2},
                #         )
                #         # 콤보 초기 선택값 기준으로 입력 필드 표시 상태 동기화
                #         on_xml_seq_changed(ext)
                ui.Spacer(height=6)
                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
                ui.Spacer(height=6)
                with ui.Frame(style={"background_color": 0xFF1E2530}):
                    with ui.VStack(padding=8, spacing=6):
                        with ui.HStack(spacing=10, height=28):
                            ui.Label(
                                "시뮬레이션 (simpy)",
                                width=150,
                                height=24,
                                style={"color": 0xFFDDDDDD},
                            )
                            ext._sim_multi_split_row = ui.HStack(spacing=8, height=26)
                            ext._sim_multi_split_row.visible = False
                            with ext._sim_multi_split_row:
                                ui.Label(
                                    "시뮬 화면(USD 로드 시)",
                                    width=130,
                                    height=22,
                                    style={"color": 0xFF9AA4B2},
                                )
                                ext._sim_split_cb_models = []
                                for i in range(1, 5):
                                    m = ui.SimpleBoolModel(i == 1)
                                    ext._sim_split_cb_models.append(m)
                                    ui.Label(f"{i}화면", width=40, height=22, style={"color": 0xFFDDDDDD})
                                    ui.CheckBox(model=m, width=22, style=CHECKBOX_WHITE_STYLE)
                                    try:
                                        m.add_value_changed_fn(lambda md, ii=i: _on_sim_split_choice_changed(ext, ii, md))
                                    except Exception:
                                        pass
                                ext._sync_sim_multi_split_ui_fn = lambda: _sync_sim_split_checkboxes_from_ext_count(ext)
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("LOT 수", width=80)
                            ui.IntField(model=ext._sim_lot_count_model, width=80)
                            ui.Label("EP 개수", width=55)
                            ext._sim_ep_count_combo = ui.ComboBox(0, "2", "3")
                            ext._sim_ep_count_combo.model.add_item_changed_fn(lambda m, *a: on_sim_ep_count_changed(ext))
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("LOT생성간격", width=100)
                            ui.FloatField(model=ext._sim_lot_spawn_min_model, width=65)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_lot_spawn_max_model, width=65)
                            ui.Label("회수간격", width=60)
                            ui.FloatField(model=ext._sim_pickup_evt_min_model, width=55)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_pickup_evt_max_model, width=55)
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("FOUP공정(EP)", width=100)
                            ui.FloatField(model=ext._sim_foup_proc_min_model, width=65)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_foup_proc_max_model, width=65)
                            ui.Label("초", width=20, style={"color": 0xFF9AA4B2})
                        ui.Label("초기 LOT 적재 포트 (체크 시 시작 시점에 FULL)", height=20)
                        with ui.HStack(spacing=8, height=26):
                            ui.Label("IN/OUT", width=55); ui.CheckBox(model=ext._sim_init_inout_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("BP1", width=30); ui.CheckBox(model=ext._sim_init_bp1_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("BP2", width=30); ui.CheckBox(model=ext._sim_init_bp2_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("BP3", width=30); ui.CheckBox(model=ext._sim_init_bp3_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ext._sim_init_bp4_row = ui.HStack(spacing=8, height=26)
                            with ext._sim_init_bp4_row:
                                ui.Label("BP4", width=30); ui.CheckBox(model=ext._sim_init_bp4_model, width=30, style=CHECKBOX_WHITE_STYLE)
                        with ui.HStack(spacing=8, height=26):
                            ui.Label("EP1", width=30); ui.CheckBox(model=ext._sim_init_ep1_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("EP2", width=30); ui.CheckBox(model=ext._sim_init_ep2_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ext._sim_init_ep3_row = ui.HStack(spacing=8, height=26)
                            with ext._sim_init_ep3_row:
                                ui.Label("EP3", width=30); ui.CheckBox(model=ext._sim_init_ep3_model, width=30, style=CHECKBOX_WHITE_STYLE)
                        try:
                            ext._sim_init_ep3_model.add_value_changed_fn(lambda m: on_sim_ep_count_changed(ext))
                        except Exception:
                            pass
                        for mdl in (
                            ext._sim_init_inout_model,
                            ext._sim_init_bp1_model,
                            ext._sim_init_bp2_model,
                            ext._sim_init_bp3_model,
                            ext._sim_init_bp4_model,
                            ext._sim_init_ep1_model,
                            ext._sim_init_ep2_model,
                            ext._sim_init_ep3_model,
                        ):
                            try:
                                mdl.add_value_changed_fn(lambda m: _sync_ep3_port_cell_visibility(ext))
                            except Exception:
                                pass
                        on_sim_ep_count_changed(ext)
                        ui.Spacer(height=2)
                        ui.Label("고장(비가동) 포트 (체크 시 해당 포트는 라우팅에서 제외, 실행 중에도 즉시 반영)", height=20)

                        def _collect_faulty_ports() -> Set[str]:
                            out: Set[str] = set()
                            try:
                                if ext._sim_fault_inout_model.get_value_as_bool():
                                    out.add("INOUT")
                                if ext._sim_fault_bp1_model.get_value_as_bool():
                                    out.add("BP1")
                                if ext._sim_fault_bp2_model.get_value_as_bool():
                                    out.add("BP2")
                                if ext._sim_fault_bp3_model.get_value_as_bool():
                                    out.add("BP3")
                                if ext._sim_fault_bp4_model.get_value_as_bool():
                                    out.add("BP4")
                                if ext._sim_fault_ep1_model.get_value_as_bool():
                                    out.add("EP1")
                                if ext._sim_fault_ep2_model.get_value_as_bool():
                                    out.add("EP2")
                                if ext._sim_fault_ep3_model.get_value_as_bool():
                                    out.add("EP3")
                            except Exception:
                                pass
                            return out

                        def _on_faulty_changed(_m=None):
                            # 엔진이 동작 중이면 즉시 반영: 다음 선택부터 고장포트 회피
                            for eng in list(getattr(ext, "_sim_engines", None) or []):
                                if eng is not None:
                                    try:
                                        eng.kick_serial_flow()
                                    except Exception:
                                        pass
                            sim = getattr(ext, "_sim_engine", None)
                            if sim is not None:
                                try:
                                    sim.kick_serial_flow()
                                except Exception:
                                    pass

                        with ui.HStack(spacing=8, height=26):
                            ui.Label("IN/OUT", width=55); ui.CheckBox(model=ext._sim_fault_inout_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("BP1", width=30); ui.CheckBox(model=ext._sim_fault_bp1_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("BP2", width=30); ui.CheckBox(model=ext._sim_fault_bp2_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("BP3", width=30); ui.CheckBox(model=ext._sim_fault_bp3_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ext._sim_fault_bp4_row = ui.HStack(spacing=8, height=26)
                            with ext._sim_fault_bp4_row:
                                ui.Label("BP4", width=30); ui.CheckBox(model=ext._sim_fault_bp4_model, width=30, style=CHECKBOX_WHITE_STYLE)
                        with ui.HStack(spacing=8, height=26):
                            ui.Label("EP1", width=30); ui.CheckBox(model=ext._sim_fault_ep1_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("EP2", width=30); ui.CheckBox(model=ext._sim_fault_ep2_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ext._sim_fault_ep3_row = ui.HStack(spacing=8, height=26)
                            with ext._sim_fault_ep3_row:
                                ui.Label("EP3", width=30); ui.CheckBox(model=ext._sim_fault_ep3_model, width=30, style=CHECKBOX_WHITE_STYLE)
                        for mdl in (
                            ext._sim_fault_inout_model,
                            ext._sim_fault_bp1_model,
                            ext._sim_fault_bp2_model,
                            ext._sim_fault_bp3_model,
                            ext._sim_fault_bp4_model,
                            ext._sim_fault_ep1_model,
                            ext._sim_fault_ep2_model,
                            ext._sim_fault_ep3_model,
                        ):
                            try:
                                mdl.add_value_changed_fn(lambda m: _on_faulty_changed(m))
                            except Exception:
                                pass
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("OHT→IN/OUT/EP", width=100)
                            ui.FloatField(model=ext._sim_oht_bp1_min_model, width=70)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_oht_bp1_max_model, width=70)
                            ui.Label("IN/OUT->BP", width=60)
                            ui.FloatField(model=ext._sim_bp1_bp_min_model, width=55)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_bp1_bp_max_model, width=55)
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("BP->EP", width=80)
                            ui.FloatField(model=ext._sim_bp_ep_min_model, width=70)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_bp_ep_max_model, width=70)
                            ui.Label("EP->OHT", width=60)
                            ui.FloatField(model=ext._sim_ep_oht_min_model, width=55)
                            ui.Label("~", width=10)
                            ui.FloatField(model=ext._sim_ep_oht_max_model, width=55)
                        with ui.HStack(spacing=8, height=28):
                            ui.Label("시뮬 속도배율", width=100)
                            ui.FloatField(model=ext._sim_speed_model, width=80)
                            ui.Label("로그주기(s)", width=70)
                            ui.FloatField(model=ext._sim_log_interval_model, width=70)
                        with ui.HStack(spacing=8, height=28):
                            ui.CheckBox(model=ext._sim_process_time_priority_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("공정설정 시간 우선", width=120)
                            ui.CheckBox(model=ext._sim_confirm_each_step_model, width=30, style=CHECKBOX_WHITE_STYLE)
                            ui.Label("각 공정 확인", width=80)
                            ui.Spacer(width=8)
                            ui.Button("시작", width=72, clicked_fn=lambda: on_sim_start_clicked(ext))
                            ui.Button("정지", width=72, clicked_fn=lambda: on_sim_stop_clicked(ext))
                            ui.Button("리셋", width=72, clicked_fn=lambda: on_sim_reset_clicked(ext))
                        ext._sim_per_screen_block = ui.VStack(spacing=4, visible=False)
                        with ext._sim_per_screen_block:
                            ui.Label(
                                "2~4분할 시: 화면별로 아래에서 「현재 설정 저장」을 누르면 LOT·간격·적재/고장·시간 값이 "
                                "해당 화면에 고정됩니다. 저장 안 한 화면은 시뮬 시작 시 제어창 값으로 자동 채웁니다.",
                                word_wrap=True,
                                width=0,
                                style={"color": 0xFF9AA4B2},
                            )
                            ext._sim_per_screen_row_hstacks = []
                            ext._sim_per_screen_status_labels = []
                            for si in range(1, 5):
                                row = ui.HStack(spacing=8, height=26, visible=False)
                                ext._sim_per_screen_row_hstacks.append(row)
                                with row:
                                    ui.Label(f"화면{si}", width=44, height=22, style={"color": 0xFFDDDDDD})
                                    st = ui.Label("(미저장)", width=64, height=22, style={"color": 0xFF9AA4B2})
                                    ext._sim_per_screen_status_labels.append(st)
                                    ui.Button(
                                        "현재 설정 저장",
                                        width=110,
                                        height=24,
                                        clicked_fn=lambda e=ext, k=si: _on_save_sim_settings_to_screen(e, k),
                                    )
                        ext._sync_sim_per_screen_rows_fn = lambda: _refresh_sim_per_screen_rows(ext)
                        with ui.HStack(spacing=8, height=24):
                            ui.Button("진행현황+Sim로그 복사", width=160, clicked_fn=lambda: on_copy_sim_progress(ext))
                        # 모니터 영역(포트/EP타임라인/진행/이력)은 높이가 커질 수 있어
                        # 레이아웃 누적/확장 버그가 화면을 밀어내지 않도록 고정 높이 스크롤 컨테이너로 감싼다.
                        ext._sim_monitor_split_host = ui.ScrollingFrame(
                            height=520,
                            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
                        )
                        ext._rebuild_sim_monitor_split_ui_fn = lambda: _rebuild_sim_monitor_split_ui(ext)
                        _rebuild_sim_monitor_split_ui(ext)
                        ext._sim_port_state_label = ui.Label("", word_wrap=False, width=0, height=0, visible=False)
                        on_sim_ep_count_changed(ext)
                        # 초기 1회: 제어창 UI 값으로 기본 스냅샷(화면1)을 채운다.
                        _sync_default_sim_snapshot_from_ui(ext)
                ui.Spacer(height=6)
                ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
                ui.Spacer(height=8)
                ui.Label("우선 표시 이름 규칙 (접두사, 비우면 순서대로 표시)", height=20)
                ui.StringField(model=ext._priority_prefix_model, height=22)
                # 요구사항: “우선표시 이름 규칙” 아래 영역은 당분간 사용하지 않으므로 숨김 처리.
                # (주석처리 수준으로 UI에서 안 보이게)
                if False:
                    ui.Spacer(height=4)
                    ui.Label("로드된 USD 내 장비 prim (드롭다운)", height=20)
                    ui.Button("목록 새로고침", height=28, clicked_fn=lambda: on_refresh_prim_list(ext))
                    ui.Spacer(height=4)
                    with ui.ScrollingFrame(height=280, style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                        ext._object_list_frame = ui.VStack(spacing=4, alignment=ui.Alignment.LEFT_TOP)
    ext._sync_sim_multi_split_row_visibility_fn = _sync_sim_multi_split_row_visibility
    # 우선표시 이름 규칙 아래 UI를 숨겼으므로 목록 refresh도 비활성
    # refresh_object_list(ext)
    try:
        sim_multi_view.attach_stage_visibility_subscription(
            ext, lambda: _sync_sim_multi_split_row_visibility(ext)
        )
        _sync_sim_multi_split_row_visibility(ext)
        _refresh_sim_per_screen_rows(ext)
    except Exception:
        pass

    # -------------------------------------------------------------------
    # “기본값은 제어창 한 군데만 수정”을 위한 자동 동기화:
    # - UI 모델이 바뀌면 화면1 스냅샷(기본값) 갱신
    # - 저장하지 않은 화면(None)만 기본값을 따라가게 채움
    # -------------------------------------------------------------------
    try:
        for mdl in (
            getattr(ext, "_sim_lot_count_model", None),
            getattr(ext, "_sim_lot_spawn_min_model", None),
            getattr(ext, "_sim_lot_spawn_max_model", None),
            getattr(ext, "_sim_pickup_evt_min_model", None),
            getattr(ext, "_sim_pickup_evt_max_model", None),
            getattr(ext, "_sim_oht_bp1_min_model", None),
            getattr(ext, "_sim_oht_bp1_max_model", None),
            getattr(ext, "_sim_bp1_bp_min_model", None),
            getattr(ext, "_sim_bp1_bp_max_model", None),
            getattr(ext, "_sim_bp_ep_min_model", None),
            getattr(ext, "_sim_bp_ep_max_model", None),
            getattr(ext, "_sim_ep_oht_min_model", None),
            getattr(ext, "_sim_ep_oht_max_model", None),
            getattr(ext, "_sim_init_inout_model", None),
            getattr(ext, "_sim_init_bp1_model", None),
            getattr(ext, "_sim_init_bp2_model", None),
            getattr(ext, "_sim_init_bp3_model", None),
            getattr(ext, "_sim_init_bp4_model", None),
            getattr(ext, "_sim_init_ep1_model", None),
            getattr(ext, "_sim_init_ep2_model", None),
            getattr(ext, "_sim_init_ep3_model", None),
            getattr(ext, "_sim_fault_inout_model", None),
            getattr(ext, "_sim_fault_bp1_model", None),
            getattr(ext, "_sim_fault_bp2_model", None),
            getattr(ext, "_sim_fault_bp3_model", None),
            getattr(ext, "_sim_fault_bp4_model", None),
            getattr(ext, "_sim_fault_ep1_model", None),
            getattr(ext, "_sim_fault_ep2_model", None),
            getattr(ext, "_sim_fault_ep3_model", None),
        ):
            if mdl is None:
                continue
            try:
                mdl.add_value_changed_fn(lambda _m, e=ext: _sync_default_sim_snapshot_from_ui(e))
            except Exception:
                pass
    except Exception:
        pass


def on_xml_seq_changed(ext: Any) -> None:
    try:
        idx = ext._xml_seq_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    seqs = [
        xml_generator.SEQ_READYTOLOAD,
        xml_generator.SEQ_ARRIVED,
        xml_generator.SEQ_MOVE_TRANSFERING,
        xml_generator.SEQ_MOVE,
        xml_generator.SEQ_READYTOUNLOAD,
        xml_generator.SEQ_REMOVED,
    ]
    seq = seqs[idx] if 0 <= idx < len(seqs) else xml_generator.SEQ_READYTOLOAD
    ext._xml_ab_inputs_frame.visible = seq in xml_generator.FROM_TO_SEQS
    ext._xml_port_inputs_frame.visible = seq in xml_generator.PORT_ID_ONLY_SEQS


def on_xml_ok_clicked(ext: Any) -> None:
    try:
        idx = ext._xml_seq_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    seqs = [
        xml_generator.SEQ_READYTOLOAD,
        xml_generator.SEQ_ARRIVED,
        xml_generator.SEQ_MOVE_TRANSFERING,
        xml_generator.SEQ_MOVE,
        xml_generator.SEQ_READYTOUNLOAD,
        xml_generator.SEQ_REMOVED,
    ]
    seq = seqs[idx] if 0 <= idx < len(seqs) else xml_generator.SEQ_READYTOLOAD
    try:
        if seq in xml_generator.FROM_TO_SEQS:
            from_port = ext._xml_from_port_model.get_value_as_int()
            to_port = ext._xml_to_port_model.get_value_as_int()
            xml = xml_generator.build_xml_string(seq, from_port_id=from_port, to_port_id=to_port)
        else:
            port_id = ext._xml_port_id_model.get_value_as_int()
            xml = xml_generator.build_xml_string(seq, port_id=port_id)
        ext._last_generated_xml = xml
        print(xml, flush=True)
    except Exception as e:
        print(f"[morph.tbs_control_2][xml_generator] XML 생성 실패: {e}", flush=True)


def on_xml_run_clicked(ext: Any) -> None:
    xml_text = (ext._last_generated_xml or "").strip()
    if not xml_text:
        print("[morph.tbs_control_2][xml_generator] 저장된 XML이 없습니다. 먼저 OK로 XML을 생성하세요.", flush=True)
        return
    parsed = xml_generator.parse_xml_string(xml_text)
    if not parsed:
        print("[morph.tbs_control_2][xml_generator] XML 역파싱 실패.", flush=True)
        return
    lines = ["[XML PARSE RESULT]"]
    if parsed.get("action_desc"):
        lines.append("[ACTION]")
        lines.append(parsed.get("action_desc", ""))

    for k in (
        "sequence_name",
        "destination",
        "origination",
        "tid",
        "facility",
        "equipment_id",
        "port_id",
        "from_port_id",
        "to_port_id",
    ):
        lines.append(f"{k} = {parsed.get(k, '')}")
    print("\n".join(lines), flush=True)


def _append_sim_log_channel(ext: Any, screen: int, msg: str) -> None:
    """
    멀티 모니터에서 **지정 화면**의 ``history_label`` 에만 한 줄(또는 누적 블록)을 붙인다.

    - ``screen`` 은 1-based; ``_sim_monitor_channels[screen-1]`` 을 사용한다.
    - 최대 약 200줄을 넘기면 앞부분을 잘라 메모리·UI 부하를 줄인다.
    """
    if not msg:
        return
    chans = getattr(ext, "_sim_monitor_channels", None)
    if not isinstance(chans, list) or not chans:
        return
    try:
        idx = int(screen) - 1
    except Exception:
        idx = 0
    if idx < 0 or idx >= len(chans):
        idx = 0
    ch = chans[idx]
    if not isinstance(ch, dict):
        return
    lbl = ch.get("history_label")
    prev = (lbl.text if lbl is not None else "").strip()
    merged = f"{prev}\n{msg}".strip() if prev else msg
    rows = merged.splitlines()
    if len(rows) > 200:
        merged = "\n".join(rows[-200:])
    if lbl is not None:
        lbl.text = merged


def _append_sim_log(ext: Any, line: str) -> None:
    """
    UI 스레드 전용: 이력 로그 패널에 줄 추가. 시뮬 워커는 ``post_sim_history_line`` 로 큐에 넣는다.

    - 멀티(``_sim_monitor_channels`` 길이>1)이면 원문의 ``[화면N]`` 접두(엔진 on_log)로 채널을 고른 뒤,
      **접두 뒤 본문만** ``_format_history_line`` 한다. (포맷 단계에서 줄 앞에 이모지가 붙으면
      줄 처음의 ``[화면N]`` 패턴 매칭이 깨져 전부 화면1로 가는 문제를 피한다.)
    - 접두가 없으면 전역 메시지로 보고 화면1에만 붙인다.
    - 단일 모드는 ``_sim_history_text`` / ``_sim_history_label`` 레거시 경로.
    """
    raw = (line or "").strip()
    if not raw:
        return
    # 요구사항: SIM 현황(진행현황 아래 [SIM] 이력 영역)에는 타임테이블만 깔끔하게 남긴다.
    # - 일반 로그/안내(프리런 시작/완료 등)는 콘솔로만 보고, 이력 영역은 타임테이블 전용으로 유지.
    # - 타임테이블 블록은 _build_prerun_timetable_text에서 "[SIM] 타임테이블(프리런)" 헤더로 시작한다.
    try:
        only_tb = bool(getattr(ext, "_sim_history_timetable_only", True))
    except Exception:
        only_tb = True
    if only_tb:
        if not (raw.startswith("[SIM] 타임테이블(프리런)") or raw.startswith("[화면")):
            return
    chans = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans, list) and len(chans) > 1:
        m0 = re.match(r"^\[화면(\d+)\]\s*", raw)
        if m0:
            try:
                si = int(m0.group(1))
            except Exception:
                si = 1
            try:
                si = max(1, min(len(chans), si))
            except Exception:
                si = 1
            rest = raw[m0.end() :].strip() if m0.end() <= len(raw) else raw
            msg = _format_history_line(rest)
            if not msg:
                return
            _append_sim_log_channel(ext, si, msg)
            return
        msg = _format_history_line(raw)
        if not msg:
            return
        _append_sim_log_channel(ext, 1, msg)
        return

    msg = _format_history_line(raw)
    if not msg:
        return
    prev = ext._sim_history_text.as_string if getattr(ext, "_sim_history_text", None) else ""
    merged = f"{prev}\n{msg}".strip() if prev else msg
    # 화면이 너무 길어지지 않게 최근 200줄만 유지
    rows = merged.splitlines()
    if len(rows) > 200:
        merged = "\n".join(rows[-200:])
    if getattr(ext, "_sim_history_text", None):
        ext._sim_history_text.set_value(merged)
    if getattr(ext, "_sim_history_label", None) is not None:
        ext._sim_history_label.text = merged


def _format_history_line(line: str) -> str:
    """
    이력로그 가독성을 위해 핵심 토큰을 앞에 배치하고 태그를 단순화한다.
    원문 정보는 유지하되 읽기만 쉽게 만든다.
    """
    s = (line or "").strip()
    if not s:
        return ""
    # 자주 보이는 태그를 직관적인 짧은 라벨로 치환
    tag_map = {
        "[STORY]": "[스토리]",
        "[MOVE]": "[이송]",
        "[ARRIVED]": "[도착]",
        "[PROCESS]": "[공정]",
        "[READYTOLOAD]": "[대기준비]",
        "[READYTOUNLOAD]": "[반출준비]",
        "[REMOVED]": "[반출완료]",
        "[INPUT]": "[투입]",
        "[WAIT]": "[대기]",
        "[SUMMARY LOT]": "[LOT 요약]",
        "[SUMMARY]": "[요약]",
    }
    for old, new in tag_map.items():
        if old in s:
            s = s.replace(old, new)
    # 시뮬 이벤트 원문은 너무 길어져서 핵심만 요약
    if "[SIM EVENT" in s and "seq=" in s:
        try:
            part = s.split("] ", 1)
            head = part[0] + "]" if len(part) == 2 else "[SIM EVENT]"
            body = part[1] if len(part) == 2 else s
            seq = ""
            lot = ""
            fr = ""
            to = ""
            for tok in body.split():
                if tok.startswith("seq="):
                    seq = tok[4:]
                elif tok.startswith("lot="):
                    lot = tok[4:]
                elif tok.startswith("from="):
                    fr = tok[5:]
                elif tok.startswith("to="):
                    to = tok[3:]
            route = f"{fr}->{to}" if fr and to else (to or fr or "-")
            return _with_history_color_icon(f"{head} [이벤트] seq={seq} lot={lot} route={route}")
        except Exception:
            return _with_history_color_icon(s)
    return _with_history_color_icon(s)


def _with_history_color_icon(s: str) -> str:
    """
    단일 Label 제약에서 줄 단위 강조를 위해 색상 아이콘을 앞에 붙인다.
    🟥 오류/실패, 🟨 대기/주의, 🟩 완료/성공, 🟦 이벤트/진행, ⬜ 일반
    """
    t = (s or "").upper()
    if any(k in t for k in ("실패", "ERROR", "EXCEPTION", "예외", "파싱실패")):
        return f"🟥 {s}"
    if any(k in t for k in ("대기", "[WAIT]", "파일없음", "매핑없음", "주의")):
        return f"🟨 {s}"
    if any(k in t for k in ("완료", "DONE", "저장 완료", "실행시작", "실행준비완료")):
        return f"🟩 {s}"
    if any(k in t for k in ("이벤트", "MOVE", "ARRIVED", "PROCESS", "STORY", "투입", "이송", "공정", "도착")):
        return f"🟦 {s}"
    return f"⬜ {s}"


def _append_anim_history_log(ext: Any, line: str) -> None:
    """애니메이션 실행이력 패널 제거됨. 호출은 호환을 위해 유지한다."""
    return


def _render_pending_dots(ext: Any) -> None:
    """(요청으로 제거) 점 표시 기능 비활성화."""
    return


def _port_cell_text(occ: Dict[str, Any], port: str) -> str:
    v = str(occ.get(port, "-")).strip()
    if not v or v.upper() in ("EMPTY", "-", "NONE"):
        return "-"
    if v.upper() == "FULL":
        return "FULL"
    return v


def _compact_cell_value(v: str, max_len: int = 10) -> str:
    s = (v or "-").strip()
    if len(s) <= max_len:
        return s
    return s[: max(1, max_len - 1)] + "..."


def _ep_count_idx_for_port_panel(ext: Any, screen_1based: int) -> int:
    """
    포트 상태 패널에서 BP4/EP3 칸 표시용 (0=EP2구성, 1=EP3구성).

    멀티 분할 시 화면별 「현재 설정 저장」스냅샷이 있으면 그 값을 쓰고,
    없으면 "화면1 스냅샷(기본값)"을 따른다.
    (요구사항: 화면2~4는 저장 전까지 현재 UI 변경의 영향을 받지 않아야 함)
    """
    try:
        si = int(screen_1based)
    except Exception:
        si = 1
    if si < 1:
        si = 1
    try:
        snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [])
        idx = si - 1
        if 0 <= idx < len(snaps) and isinstance(snaps[idx], dict):
            return int(snaps[idx].get("ep_count_idx", 0) or 0)
        # 화면2~4가 미저장(None)인 경우: 화면1 기본값을 폴백으로 사용
        if si > 1 and len(snaps) >= 1 and isinstance(snaps[0], dict):
            return int(snaps[0].get("ep_count_idx", 0) or 0)
    except Exception:
        pass
    try:
        return int(ext._sim_ep_count_combo.model.get_item_value_model().as_int)
    except Exception:
        return 0


def _sync_ep3_port_cell_visibility_for_channel(ext: Any, ch: Dict[str, Any]) -> None:
    container = ch.get("port_ep3_cell_container")
    bp4_container = ch.get("port_bp4_cell_container")
    try:
        si = int(ch.get("screen", 1) or 1)
    except Exception:
        si = 1
    ep_idx = _ep_count_idx_for_port_panel(ext, si)
    # 일부 환경에서 체크 이벤트 반영이 지연되는 문제를 피하기 위해
    # EP 개수=3이면 EP3 칸은 항상 보이게 유지하고, 체크 여부는 초기 적재 로직에서 사용.
    is_ep3 = bool(ep_idx == 1)
    if container is not None:
        container.visible = is_ep3
    if bp4_container is not None:
        bp4_container.visible = is_ep3


def _sync_ep3_port_cell_visibility(ext: Any) -> None:
    chans = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans, list) and len(chans) > 0:
        for ch in chans:
            if isinstance(ch, dict):
                _sync_ep3_port_cell_visibility_for_channel(ext, ch)
        return
    ch = {
        "port_ep3_cell_container": getattr(ext, "_sim_port_ep3_cell_container", None),
        "port_bp4_cell_container": getattr(ext, "_sim_port_bp4_cell_container", None),
    }
    _sync_ep3_port_cell_visibility_for_channel(ext, ch)


def _set_port_box_style(ext: Any, port: str, value: str, cell_boxes: Any = None) -> None:
    boxes = cell_boxes if cell_boxes is not None else (getattr(ext, "_sim_port_cell_boxes", {}) or {})
    box = boxes.get(port) if isinstance(boxes, dict) else None
    if box is None:
        return
    v = (value or "").strip().upper()
    if not v or v in ("-", "EMPTY", "NONE"):
        fill = 0xFF2A2F38
    elif v == "FULL":
        fill = 0xFF6B5B2A
    else:
        fill = 0xFF1F4A36
    try:
        box.style = {"background_color": fill, "border_color": 0xFF7B8799, "border_width": 1}
    except Exception:
        pass


def _update_port_occupancy_panel(ext: Any, occ: Dict[str, Any], sim_time: str = "", screen: int = 1) -> None:
    if not isinstance(occ, dict):
        return
    # 중요(포트상태 "전부 비어버림" 방지):
    # occ가 빈 dict로 들어오면 _port_cell_text 기본값이 "-"가 되어 포트상태가 통째로 비어 보인다.
    # 타임라인 재생/프리런 플레이백에서 간헐적으로 occ가 누락/빈 값이 들어올 수 있으므로,
    # 이 경우에는 마지막 스냅샷으로 폴백하고, 폴백도 없으면 UI를 덮어쓰지 않는다.
    if not occ:
        try:
            by_prev = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
            if isinstance(by_prev, dict):
                occ_prev = by_prev.get(str(int(screen) if int(screen) > 0 else 1))
                if isinstance(occ_prev, dict) and occ_prev:
                    occ = dict(occ_prev)
        except Exception:
            pass
        if not occ:
            return
    ch: Optional[Dict[str, Any]] = None
    chans = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans, list) and len(chans) > 0:
        try:
            si = int(screen)
        except Exception:
            si = 1
        si = max(1, min(len(chans), si))
        ch = chans[si - 1] if isinstance(chans[si - 1], dict) else None
    inout = _port_cell_text(occ, "INOUT")
    bp1 = _port_cell_text(occ, "BP1")
    bp2 = _port_cell_text(occ, "BP2")
    bp3 = _port_cell_text(occ, "BP3")
    bp4 = _port_cell_text(occ, "BP4")
    ep1 = _port_cell_text(occ, "EP1")
    ep2 = _port_cell_text(occ, "EP2")
    ep3 = _port_cell_text(occ, "EP3")
    t = str(sim_time).strip()
    scr_lbl = int((ch or {}).get("screen", screen) or screen)
    hdr = (ch or {}).get("port_header") if ch else getattr(ext, "_sim_port_state_header_label", None)
    # 헤더의 t= 깜빡임 방지:
    # - 어떤 경로(예: 레이아웃 재조립 직후 복원)에서는 sim_time을 빈 문자열로 호출할 수 있다.
    # - 이때 헤더를 "[포트상태·화면N]"으로 덮어쓰면 t=가 사라졌다가, 다음 정상 업데이트에서 다시 나타나는
    #   깜빡임이 발생한다.
    # - 따라서 t가 비어있으면 헤더 텍스트는 유지(덮어쓰지 않음)한다.
    if hdr is not None and t:
        head = f"[포트상태·화면{scr_lbl} t={t}]"
        hdr.text = head
    _sync_ep3_port_cell_visibility(ext)
    cells = (ch or {}).get("port_cells") if ch else (getattr(ext, "_sim_port_cells", {}) or {})
    boxes = (ch or {}).get("port_cell_boxes") if ch else (getattr(ext, "_sim_port_cell_boxes", {}) or {})
    if not isinstance(cells, dict):
        cells = {}
    if not isinstance(boxes, dict):
        boxes = {}
    if "INOUT" in cells:
        cells["INOUT"].text = f"IN/OUT:{_compact_cell_value(inout)}"
        _set_port_box_style(ext, "INOUT", inout, boxes)
    if "BP1" in cells:
        cells["BP1"].text = f"BP1:{_compact_cell_value(bp1)}"
        _set_port_box_style(ext, "BP1", bp1, boxes)
    if "BP2" in cells:
        cells["BP2"].text = f"BP2:{_compact_cell_value(bp2)}"
        _set_port_box_style(ext, "BP2", bp2, boxes)
    if "BP3" in cells:
        cells["BP3"].text = f"BP3:{_compact_cell_value(bp3)}"
        _set_port_box_style(ext, "BP3", bp3, boxes)
    if "BP4" in cells:
        cells["BP4"].text = f"BP4:{_compact_cell_value(bp4)}"
        _set_port_box_style(ext, "BP4", bp4, boxes)
    if "EP1" in cells:
        cells["EP1"].text = f"EP1:{_compact_cell_value(ep1)}"
        _set_port_box_style(ext, "EP1", ep1, boxes)
    if "EP2" in cells:
        cells["EP2"].text = f"EP2:{_compact_cell_value(ep2)}"
        _set_port_box_style(ext, "EP2", ep2, boxes)
    ep3_cell = (ch or {}).get("port_ep3_cell") if ch else getattr(ext, "_sim_port_ep3_cell", None)
    if ep3_cell is not None:
        ep3_cell.text = f"EP3:{_compact_cell_value(ep3)}"
        _set_port_box_style(ext, "EP3", ep3, boxes)

    # 마지막 점유 스냅샷 저장(빈 dict는 저장하지 않음)
    try:
        by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
        if not isinstance(by, dict):
            by = {}
            ext._sim_last_ports_occupancy_by_screen = by
        sk = str(int((ch or {}).get("screen", screen) or screen))
        if occ:
            by[sk] = dict(occ)
    except Exception:
        pass

    # 포트상태 아래 EP 타임라인(전용 영역) 갱신 — 멀티 채널 UI가 없어도(USD 미로드 등) 스냅샷/웹 막대용 상태는 누적
    try:
        ch_tl = ch if ch is not None else {"screen": int(screen), "ep_timeline_host": None}
        _update_ep_timeline_under_port_state(ext, ch_tl, occ, t)
    except Exception:
        pass


def _update_ep_timeline_under_port_state(ext: Any, ch: Dict[str, Any], occ: Dict[str, Any], sim_time_text: str) -> None:
    """포트상태 영역 바로 아래의 EP 타임라인 3줄(EP1/EP2(/EP3)/ALL_EP) + 시간 라벨."""
    host = ch.get("ep_timeline_host")
    try:
        screen = int(ch.get("screen", 1))
    except Exception:
        screen = 1
    scr_key = str(screen)
    try:
        t_now = float(str(sim_time_text or "0").strip() or "0.0")
    except Exception:
        return

    # 막대그래프 점프(버스트) 방지:
    # - sim_time_text는 UI 큐 드레인/렌더 지연에 따라 띄엄띄엄 갱신될 수 있다.
    # - 그 경우 dt가 크게 잡혀 세그먼트가 한 번에 누적되며 막대가 "몇 배 빨리" 채워진 것처럼 보인다.
    # - 해결: bar 누적에는 wall-clock 기반의 virtual time을 사용해 일정 속도로 전진시키고,
    #   target(sim_time)을 따라가되 한 프레임에 큰 dt를 먹지 않도록 clamp 한다.
    try:
        vt_by = getattr(ext, "_sim_ep_timeline_virtual_time_by_screen", None)
        if not isinstance(vt_by, dict):
            vt_by = {}
            ext._sim_ep_timeline_virtual_time_by_screen = vt_by
    except Exception:
        vt_by = {}
        try:
            ext._sim_ep_timeline_virtual_time_by_screen = vt_by
        except Exception:
            pass
    try:
        vprev = float(vt_by.get(scr_key, 0.0) or 0.0)
    except Exception:
        vprev = 0.0
    try:
        # reset/재시작 등으로 target이 감소하면 즉시 맞춘다.
        if t_now + 1e-9 < vprev:
            vprev = float(t_now)
    except Exception:
        pass
    try:
        now_wall = float(time.perf_counter())
    except Exception:
        now_wall = 0.0
    try:
        last_wall = float(vt_by.get(f"_wall_{scr_key}", 0.0) or 0.0)
    except Exception:
        last_wall = 0.0
    if last_wall <= 0.0 or now_wall <= 0.0:
        dt_wall = 0.0
    else:
        dt_wall = max(0.0, float(now_wall) - float(last_wall))
    try:
        vt_by[f"_wall_{scr_key}"] = float(now_wall)
    except Exception:
        pass
    # wall 기준 전진량(상한): 시뮬 배속을 반영해 bar가 sim_time 증가 속도를 따라가게 한다.
    # - 배속이 높을수록(sim_time이 더 빠르게 전진) dt_adv도 비례해서 커져야 한다.
    # - 단, 프레임 드랍/큐 지연이 있어도 "한 번에 확 뛰는" 느낌을 줄이기 위해 상한은 유지하되 배속에 비례해 확장한다.
    try:
        sp_model = getattr(ext, "_sim_speed_model", None)
        sp = float(sp_model.get_value_as_float()) if sp_model is not None else 1.0
    except Exception:
        sp = 1.0
    if sp <= 0.0:
        sp = 1.0
    # 배속 반영 전진량(초/호출)
    dt_adv_raw = float(dt_wall) * float(sp)
    # 상한(초/호출): 기본 0.20s를 배속에 비례 확장
    dt_adv_cap = 0.20 * float(sp)
    dt_adv = min(float(dt_adv_cap), float(dt_adv_raw))
    vnow = float(vprev) + float(dt_adv)
    # target(t_now)을 넘지 않도록 clamp (표시 t(sim)보다 막대가 앞서가지 않게)
    if vnow > float(t_now):
        vnow = float(t_now)
    try:
        vt_by[scr_key] = float(vnow)
    except Exception:
        pass
    # bar 누적은 virtual time 기준으로 진행(표시 라벨은 t_now 그대로 사용 가능)
    t_bar = float(vnow)

    st_by = getattr(ext, "_sim_ep_occ_timeline_state_by_screen", None)
    if not isinstance(st_by, dict):
        st_by = {}
        ext._sim_ep_occ_timeline_state_by_screen = st_by
    st = st_by.get(scr_key)
    if not isinstance(st, dict):
        st = {"t_last": None, "rows": {}, "total_est_fixed": None}
        st_by[scr_key] = st
    t_last = st.get("t_last", None)
    st["t_last"] = t_bar
    if t_last is None:
        # 첫 프레임은 누적 없이 렌더만
        dt = 0.0
    else:
        dt = max(0.0, float(t_bar) - float(t_last))

    # EP 줄은 항상 EP1/EP2를 표시하고, EP3는 설정(EP count=3)일 때만 추가한다.
    eps = ["EP1", "EP2"]
    try:
        # 중요: 분할 화면은 화면별 저장 스냅샷의 ep_count_idx를 따라야 한다.
        # (전역 콤보를 보면 모든 화면이 동일 EP 개수로 그려지는 문제가 생긴다)
        ep_idx = int(_ep_count_idx_for_port_panel(ext, int(screen)))
    except Exception:
        ep_idx = 0
    if ep_idx != 0:
        eps.append("EP3")
    rows = list(eps) + ["ALL_EP"]

    rows_state = st.get("rows", {})
    if not isinstance(rows_state, dict):
        rows_state = {}
        st["rows"] = rows_state
    for r in rows:
        if r not in rows_state or not isinstance(rows_state.get(r), list):
            rows_state[r] = []

    def _is_empty_port(ep: str) -> bool:
        v = occ.get(ep, "")
        return not bool(str(v or "").strip())

    all_empty = True
    # empty_acc:
    # - UI 막대그래프 우측에 "현재까지 누적된 EMPTY 시간(초)"을 상시 표시하기 위한 값.
    # - 이 값은 막대그래프용 rows_state(세그먼트 dur 합)에서 계산한다.
    # - 시뮬 종료 후 요약 로그에 찍히는 EP_EMPTY/ALL_EP_EMPTY는 simulation_engine.py에서
    #   tick 기반으로 별도로 누적(_ep_empty_sec/_all_ep_empty_sec)되며, 개념적으로 동일한 통계다.
    empty_acc: Dict[str, float] = {}
    for ep in eps:
        empty = _is_empty_port(ep)
        if not empty:
            all_empty = False
        if dt > 1e-9:
            segs = rows_state[ep]
            if segs and isinstance(segs[-1], dict) and bool(segs[-1].get("empty")) == bool(empty):
                segs[-1]["dur"] = float(segs[-1].get("dur", 0.0)) + float(dt)
            else:
                segs.append({"empty": bool(empty), "dur": float(dt)})
            if len(segs) > 220:
                del segs[:-200]
        # 현재까지 "EMPTY" 누적(세그먼트 합)
        try:
            empty_acc[ep] = sum(float(s.get("dur", 0.0)) for s in rows_state.get(ep, []) if isinstance(s, dict) and bool(s.get("empty", False)))
        except Exception:
            empty_acc[ep] = 0.0
    if dt > 1e-9:
        segs = rows_state["ALL_EP"]
        if segs and isinstance(segs[-1], dict) and bool(segs[-1].get("empty")) == bool(all_empty):
            segs[-1]["dur"] = float(segs[-1].get("dur", 0.0)) + float(dt)
        else:
            segs.append({"empty": bool(all_empty), "dur": float(dt)})
        if len(segs) > 220:
            del segs[:-200]
    try:
        empty_acc["ALL_EP"] = sum(float(s.get("dur", 0.0)) for s in rows_state.get("ALL_EP", []) if isinstance(s, dict) and bool(s.get("empty", False)))
    except Exception:
        empty_acc["ALL_EP"] = 0.0

    # total_est(막대 스케일): 폴백 max(30,t*1.2)로 먼저 고정된 뒤 엔진 sim_total_est 가 늦게 들어오면
    # 이전에는 상향이 안 되어 30칸이 전부 빨간 EMPTY 세그로만 채워진 것처럼 보였다 → 확정값이 더 크면 상향만 허용.
    total_est = st.get("total_est_fixed", None)
    try:
        total_est = float(total_est) if total_est is not None else None
    except Exception:
        total_est = None
    cand: Optional[float] = None
    try:
        last_te = getattr(ext, "_sim_last_total_est_by_screen", None)
        if isinstance(last_te, dict):
            cand = float(last_te.get(scr_key) or 0.0)
    except Exception:
        cand = None
    if cand is not None and cand <= 0.0:
        cand = None
    if total_est is None or total_est <= 0.0:
        if cand is not None:
            total_est = cand
        else:
            total_est = max(30.0, t_now * 1.2)
        st["total_est_fixed"] = float(total_est)
    elif cand is not None and cand > float(total_est) + 1e-3:
        st["total_est_fixed"] = float(cand)

    # ep_timeline_host 없음(레거시 단일 패널·웹 스냅샷만): 상태만 갱신하고 omni.ui 는 건너뜀
    if host is None:
        return

    BAR_W, NAME_W, VAL_W, frame_pad, row_sp = _ep_occ_timeline_layout_dims(ext)
    cur_layout = (int(BAR_W), int(NAME_W), int(VAL_W), int(frame_pad), int(row_sp))

    # 동일 시뮼 시각(dt=0)·막대 스케일·EP 점유가 같으면 VStack 전체 destroy/rebuild 생략.
    # (매 tick마다 트리를 갈아엎으면 단일 모니터에서 막대 영역 전체가 깜빡인다.)
    try:
        te_snap = float(total_est)
    except Exception:
        te_snap = 0.0
    try:
        occ_fp = tuple((str(ep), bool(_is_empty_port(ep))) for ep in eps) + (bool(all_empty),)
    except Exception:
        occ_fp = ()
    old = ch.get("ep_timeline_widget", None)
    last_te = st.get("_ep_tl_last_ui_te")
    last_fp = st.get("_ep_tl_last_ui_occ_fp")
    last_layout = st.get("_ep_tl_last_ui_layout")
    if (
        old is not None
        and dt <= 1e-9
        and isinstance(last_te, (int, float))
        and abs(float(last_te) - te_snap) <= 1e-2
        and last_fp == occ_fp
        and isinstance(last_layout, tuple)
        and len(last_layout) == 5
        and tuple(int(x) for x in last_layout) == cur_layout
    ):
        return

    # UI 렌더(고정 폭)
    if old is not None:
        try:
            old.destroy()
        except Exception:
            pass
        ch["ep_timeline_widget"] = None

    BAR_H = 14
    tick_step = max(10.0, float(int((((float(total_est) / 8.0) + 9.999) // 10.0) * 10.0)))

    def _color(empty: bool) -> int:
        return 0xFF0000FF if empty else 0xFF00FF00

    with host:
        ch["ep_timeline_widget"] = ui.VStack(spacing=6)
        with ui.Frame(style={"padding": int(frame_pad)}):
            with ui.VStack(spacing=6):
                # 시간 라벨(너무 촘촘하면 안 보이므로 최대 8개 정도만)
                with ui.HStack(height=14, spacing=0):
                    ui.Spacer(width=NAME_W)
                    with ui.ZStack(width=BAR_W, height=14):
                        ui.Rectangle(width=BAR_W, height=14, style={"background_color": 0x441A1E26})
                        # 50단위(또는 tick_step) 눈금 + 마지막에 실제 총시간(예: 342)을 끝에 추가
                        try:
                            ticks = max(1, int(float(total_est) // float(tick_step)))
                        except Exception:
                            ticks = 1
                        for i in range(ticks + 1):
                            try:
                                t_lbl = float(i) * float(tick_step)
                            except Exception:
                                t_lbl = 0.0
                            x = int(round((float(t_lbl) / float(total_est)) * float(BAR_W))) if total_est > 1e-9 else 0
                            x = max(0, min(BAR_W - 1, x))
                            with ui.Placer(offset_x=x, offset_y=0):
                                ui.Label(
                                    f"{int(round(t_lbl))}",
                                    width=36,
                                    height=14,
                                    style={"color": 0xFFE0E6F0, "font_size": 10},
                                )
                    # 끝값(정확한 총시간) 라벨은 막대 오른쪽 바깥으로 빼서(더 우측),
                    # 마지막 눈금(예: 350)과 겹치지 않게 한다.
                    try:
                        t_end_lbl = float(total_est)
                        end_txt = (
                            f"{int(round(t_end_lbl))}"
                            if abs(float(t_end_lbl) - float(int(round(t_end_lbl)))) < 1e-6
                            else f"{float(t_end_lbl):.1f}"
                        )
                    except Exception:
                        end_txt = f"{total_est:.1f}"
                    ui.Spacer(width=6)
                    ui.Label(
                        end_txt,
                        width=24,
                        height=14,
                        alignment=ui.Alignment.LEFT_CENTER,
                        style={"color": 0xFFE0E6F0, "font_size": 10},
                    )
                # 막대(EP1/EP2(/EP3)/ALL_EP) — 줄은 항상 렌더된다.
                for r in rows:
                    with ui.HStack(height=BAR_H, spacing=int(row_sp)):
                        ui.Label(r, width=NAME_W, height=BAR_H, style={"color": 0xFFBFC7D5, "font_size": 11})
                        with ui.ZStack(width=BAR_W, height=BAR_H):
                            ui.Rectangle(width=BAR_W, height=BAR_H, style={"background_color": 0xFF1A1E26})
                            segs = rows_state.get(r, []) or []
                            with ui.HStack(height=BAR_H, spacing=0):
                                used = 0
                                for s in segs:
                                    try:
                                        dur = float((s or {}).get("dur", 0.0))
                                    except Exception:
                                        dur = 0.0
                                    if dur <= 1e-9:
                                        continue
                                    w = int(round((dur / float(total_est)) * BAR_W))
                                    w = max(1, w)
                                    if used + w > BAR_W:
                                        w = max(1, BAR_W - used)
                                    used += w
                                    ui.Rectangle(
                                        width=w,
                                        height=BAR_H,
                                        style={"background_color": _color(bool((s or {}).get("empty", False)))},
                                    )
                                    if used >= BAR_W:
                                        break
                                if used < BAR_W:
                                    ui.Spacer(width=(BAR_W - used))
                        # 우측: 누적 EMPTY 시간(초) 표시
                        try:
                            v = float(empty_acc.get(r, 0.0) or 0.0)
                        except Exception:
                            v = 0.0
                        ui.Label(
                            f"{v:.1f}s",
                            width=int(VAL_W),
                            height=BAR_H,
                            style={"color": 0xFFDDDDDD, "font_size": 11},
                        )

    try:
        st["_ep_tl_last_ui_te"] = float(te_snap)
        st["_ep_tl_last_ui_occ_fp"] = occ_fp
        st["_ep_tl_last_ui_layout"] = cur_layout
    except Exception:
        pass


def _sync_all_ep_occ_timelines_from_engines(ext: Any) -> None:
    """
    멀티 시뮬에서 한 화면의 ANIM/큐 폭주로 다른 화면의 ``timeline_only`` 가 밀리면
    포트 아래 EP 막대가 멈춘 것처럼 보인다. 각 엔진 ``env.now`` 와 마지막 점유 스냅샷으로 전 화면을 한 번에 맞춘다.
    """
    chans = getattr(ext, "_sim_monitor_channels", None)
    engs = getattr(ext, "_sim_engines", None)
    if not isinstance(chans, list) or len(chans) < 2:
        return
    if not isinstance(engs, list) or len(engs) < 2:
        return
    last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
    if not isinstance(last_by, dict):
        last_by = {}
    empty_occ = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
    for i, ch in enumerate(chans):
        if not isinstance(ch, dict):
            continue
        si = i + 1
        sk = str(si)
        eng = engs[i] if i < len(engs) else None
        if eng is None or bool(getattr(eng, "is_done", False)):
            continue
        try:
            t_now = float(getattr(getattr(eng, "env", None), "now", 0.0) or 0.0)
        except Exception:
            t_now = 0.0
        occ = last_by.get(sk) if isinstance(last_by.get(sk), dict) else None
        if occ is None:
            occ = dict(empty_occ)
        try:
            _update_ep_timeline_under_port_state(ext, ch, occ, f"{t_now:.2f}")
        except Exception:
            pass


def _enqueue_sim_log(ext: Any, line: str) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        q.put_nowait((SimUiQueueKind.HISTORY_LINE, (line or "").strip()))
    except Exception:
        pass


def post_sim_history_line(ext: Any, line: str) -> None:
    """시뮬 워커 스레드에서 호출: 스토리/상태 텍스트를 '이력 로그' 패널로 보낸다."""
    _enqueue_sim_log(ext, line)


def _enqueue_anim_event(ext: Any, payload: Dict[str, str]) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        p2 = dict(payload or {})
        try:
            p2["_run_gen"] = str(int(getattr(ext, "_sim_run_gen", 0) or 0))
        except Exception:
            p2["_run_gen"] = "0"
        q.put_nowait((SimUiQueueKind.ANIM_EVENT, p2))
    except Exception:
        pass


def post_sim_anim_event(ext: Any, payload: Dict[str, str]) -> None:
    """시뮬 워커 스레드에서 호출: 애니메이션 파이프라인(포트 패널 + 애니 이력)으로 이벤트를 넘긴다."""
    _enqueue_anim_event(ext, payload)


def _enqueue_control_action(ext: Any, action: str) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        q.put_nowait((SimUiQueueKind.ACTION, action))
    except Exception:
        pass


def _enqueue_gate_request(ext: Any, payload: Dict[str, Any]) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        q.put_nowait((SimUiQueueKind.GATE, dict(payload or {})))
    except Exception:
        pass


def _show_sim_gate_dialog(ext: Any, payload: Dict[str, Any]) -> None:
    # 공정확인 모드에서는 "확인 전까지 완전 정지"가 목표이므로,
    # 확인창이 이미 떠 있으면 새 창으로 교체하지 않고(=pause가 풀리는 부작용 방지) 그냥 대기시킨다.
    if getattr(ext, "_sim_gate_dialog", None) is not None:
        return
    title = str(payload.get("title", "공정 확인"))
    msg = str(payload.get("message", "다음 공정을 진행할까요?"))
    done = payload.get("_done_event", None)
    # 웹(HTTP 브리지)에서 공정 확인을 대신 처리할 수 있도록 직렬화 가능한 요약 + done 이벤트 참조를 남긴다.
    try:
        ext._sim_web_gate_pending = {
            "title": title,
            "message": msg,
            "gate_seq_raw": str(payload.get("gate_seq_raw", "") or "").strip(),
            "gate_seq_canonical": str(payload.get("gate_seq_canonical", "") or "").strip(),
            "gate_xml_sequence_name": str(payload.get("gate_xml_sequence_name", "") or "").strip(),
        }
        ext._sim_web_gate_done_event = done
    except Exception:
        pass
    g_raw = str(payload.get("gate_seq_raw", "")).strip()
    g_can = str(payload.get("gate_seq_canonical", "")).strip()
    g_xml = str(payload.get("gate_xml_sequence_name", "")).strip()
    win_suffix = f" [{g_raw}]" if g_raw else ""
    ext._sim_gate_dialog = ui.Window(f"[SIM 확인] {title}{win_suffix}", width=580, height=400)
    with ext._sim_gate_dialog.frame:
        with ui.VStack(spacing=8, padding=10):
            with ui.Frame(style={"background_color": 0xFF2A3140, "border_width": 1, "border_color": 0xFF5A6A80}):
                with ui.VStack(spacing=4, padding=8):
                    ui.Label("이벤트 (sequence_name)", height=22, style={"color": 0xFF8EC8FF})
                    if g_raw and g_can and g_raw == g_can:
                        seq_line = f"sequence_name: {g_raw}"
                    elif g_raw or g_can:
                        seq_line = f"시뮬 seq: {g_raw or '-'}  → 규격/별칭: {g_can or '-'}"
                    else:
                        seq_line = "sequence_name: -"
                    ui.Label(seq_line, word_wrap=True, height=36)
                    if g_xml:
                        ui.Label(f"XML SEQUENCE_NAME: {g_xml}", height=22, style={"color": 0xFFC8E0FF})
            with ui.ScrollingFrame(height=240):
                with ui.VStack(spacing=4):
                    ui.Label(msg, word_wrap=True, height=200)
            with ui.HStack(spacing=8, height=30):
                ui.Button("확인", width=80, clicked_fn=lambda: _close_sim_gate_dialog(ext, done))


def _close_sim_gate_dialog(ext: Any, done_event: Any) -> None:
    w = getattr(ext, "_sim_gate_dialog", None)
    if w is not None:
        try:
            w.visible = False
            # 이벤트/드로우 중 destroy 호출 금지: 다음 프레임으로 지연
            def _defer_destroy(_e=None):
                try:
                    w.destroy()
                except Exception:
                    pass
            try:
                app.get_app().get_post_update_event_stream().create_subscription_to_pop(
                    _defer_destroy,
                    name="morph.tbs_control_2:sim_gate_destroy",
                )
            except Exception:
                pass
        except Exception:
            pass
    ext._sim_gate_dialog = None
    # 이벤트 확인창이 닫히면 gate pause 해제 (애니 pause는 별도 이벤트로 유지)
    try:
        gp = getattr(ext, "_sim_gate_pause_event", None)
        if gp is not None:
            gp.clear()
    except Exception:
        pass
    try:
        if done_event is not None:
            done_event.set()
    except Exception:
        pass
    try:
        ext._sim_web_gate_pending = None
        ext._sim_web_gate_done_event = None
    except Exception:
        pass


def _enqueue_sim_progress(ext: Any, payload: Dict[str, str]) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        q.put_nowait((SimUiQueueKind.PROGRESS, dict(payload or {})))
    except Exception:
        pass


def post_sim_progress_update(ext: Any, payload: Dict[str, str]) -> None:
    """시뮬 워커 스레드에서 호출: 공정 진행률/상태를 '진행현황' 패널로 보낸다."""
    _enqueue_sim_progress(ext, payload)


def _sim_ui_sink_progress(ext: Any, payload: Dict[str, Any]) -> None:
    _update_sim_progress(ext, payload if isinstance(payload, dict) else {})


def _build_sim_gate_request_payload(ext: Any, p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    공정확인(게이트) 창에 표시할 title/message를 구성해 큐 payload(dict)로 반환.

    목적:
    - 게이트 메시지 구성 로직을 한 함수로 모아, 유지보수 시 흐름 추적 비용을 줄인다.
    - 기능 동작(표시 순서/매핑/타이머/XML)은 기존과 동일하게 유지한다.
    """
    try:
        seq_raw = str(p.get("seq", "") or "")
        seq_can = SIM_SEQ_ALIAS.get(seq_raw.strip(), seq_raw.strip()) if seq_raw else ""
        lot = str(p.get("lot_id", "") or "")
        lot_seq = str(p.get("lot_seq", "") or "")
        foup_id = str(p.get("foup_id", "") or "")
        fr = str(p.get("from_port_id", "") or "")
        to = str(p.get("to_port_id", "") or "")
        port = str(p.get("port_id", "") or "")
        t = str(p.get("sim_time", "") or "")
        title = f"EVENT t={t}" if t else "EVENT"

        # 공정확인 창 메시지 구성(요구사항 순서):
        # 1) 발생 이벤트명  2) 이벤트 동작 설명  3) 연계 애니 파일/존재/빈파일/불필요
        # 4) TIMER(생성/회수)  5) XML 표시
        lines: List[str] = []
        lines.append(f"[EVENT] sequence_name={seq_can or '-'} (raw={seq_raw or '-'})")
        lines.append(
            f"[EVENT] lot={lot or '-'}"
            + (f" (seq={lot_seq})" if lot_seq else "")
            + (f" foup={foup_id}" if foup_id else "")
            + f" | from={fr or '-'} to={to or '-'} port={port or '-'}"
        )

        xml_text = ""
        seq_for_mapping = seq_can
        parsed: Dict[str, Any] = {}
        try:
            if seq_can in xml_generator.FROM_TO_SEQS:
                xml_text = xml_generator.build_xml_string(
                    seq_can,
                    from_port_id=_parse_port_num(fr, 1),
                    to_port_id=_parse_port_num(to, 1),
                )
            else:
                xml_text = xml_generator.build_xml_string(seq_can, port_id=_parse_port_num(port, 1))
            parsed = xml_generator.parse_xml_string(xml_text) or {}
            parsed_seq = str(parsed.get("sequence_name", "") or "").strip().upper()
            if parsed_seq:
                seq_for_mapping = parsed_seq
        except Exception:
            xml_text = ""
            parsed = {}

        # 2) 이벤트 동작 설명
        action_desc = str(parsed.get("action_desc", "") or "").strip() if isinstance(parsed, dict) else ""
        if action_desc:
            lines.append(f"[ACTION] {action_desc}")
        elif seq_for_mapping:
            lines.append(f"[ACTION] (설명 없음) seq={seq_for_mapping}")
        else:
            lines.append("[ACTION] (설명 없음)")

        # 3) 연계된 애니메이션 파일/존재여부/비어있는 파일 여부
        try:
            seq_u = str(seq_can or "").strip().upper()
            is_anim_event = seq_u in (
                str(xml_generator.SEQ_ARRIVED).strip().upper(),
                str(xml_generator.SEQ_MOVE_TRANSFERING).strip().upper(),
                str(xml_generator.SEQ_MOVE_REQ).strip().upper(),
                str(xml_generator.SEQ_REMOVED).strip().upper(),
            )
        except Exception:
            is_anim_event = False

        if not is_anim_event:
            lines.append("[ANIM] 이 이벤트는 애니메이션이 필요없는 이벤트입니다.")
        else:
            try:
                mapping_payload = dict(p or {})
                mapping_payload["seq"] = seq_for_mapping
                if parsed:
                    mapping_payload["from_port_id"] = _normalize_port_text_from_xml(str(parsed.get("from_port_id", "") or ""), fr)
                    mapping_payload["to_port_id"] = _normalize_port_text_from_xml(str(parsed.get("to_port_id", "") or ""), to)
                    mapping_payload["port_id"] = _normalize_port_text_from_xml(str(parsed.get("port_id", "") or ""), port)
                mapped_json, _meta, rule_name, source_name = _resolve_event_animation_entry(seq_for_mapping, mapping_payload)
                if not mapped_json:
                    lines.append(f"[ANIM] 매핑 없음 (event={seq_for_mapping})")
                else:
                    jp = _normalize_json_path(mapped_json)
                    exists_txt = "존재" if jp.is_file() else "없음"
                    empty_txt = ""
                    if jp.is_file():
                        try:
                            raw = json.loads(jp.read_text(encoding="utf-8"))
                            if isinstance(raw, list) and len(raw) == 0:
                                empty_txt = " / EMPTY(빈 파일)"
                        except Exception:
                            empty_txt = ""
                    lines.append(
                        f"[ANIM] file={jp.name} ({exists_txt}{empty_txt}) | source={source_name or '-'} rule={rule_name or '-'}"
                    )
            except Exception as e:
                lines.append(f"[ANIM] 매핑 확인 실패: {e}")

        # 4) TIMER
        try:
            sim = None
            me = getattr(ext, "_sim_engines", None)
            si_s = str(p.get("tbs_sim_screen", "") or "").strip()
            if isinstance(me, list) and len(me) > 0 and si_s.isdigit():
                idx = int(si_s) - 1
                if 0 <= idx < len(me):
                    sim = me[idx]
            if sim is None and isinstance(me, list) and len(me) > 0:
                sim = me[0]
            if sim is None:
                sim = getattr(ext, "_sim_engine", None)
            now_t = float(p.get("sim_time", "0.0") or 0.0)
            spawn_at = getattr(sim, "_next_spawn_at", None) if sim is not None else None
            pickup_at = getattr(sim, "_next_pickup_at", None) if sim is not None else None
            lines_t: List[str] = []
            if isinstance(spawn_at, (int, float)):
                lines_t.append(f"다음 생성까지: {max(0.0, float(spawn_at) - now_t):.2f}s (sim)")
            if isinstance(pickup_at, (int, float)):
                lines_t.append(f"다음 회수티켓까지: {max(0.0, float(pickup_at) - now_t):.2f}s (sim)")
            if lines_t:
                lines.append("")
                lines.append("TIMER:")
                lines.extend(lines_t)
        except Exception:
            pass

        # 5) XML
        if xml_text:
            lines.append("")
            lines.append("XML:")
            lines.append(xml_text)

        message = "\n".join([ln for ln in lines if ln is not None])
        return {
            "title": title,
            "message": message,
            "_done_event": threading.Event(),
            "gate_seq_raw": seq_raw,
            "gate_seq_canonical": seq_can,
            "gate_xml_sequence_name": "",
        }
    except Exception:
        return None


def _sim_ui_sink_anim_event(ext: Any, payload: Dict[str, Any], panel_mode: SimLogPanelMode) -> None:
    """
    시뮼 엔진에서 올라온 이벤트(큐 ``ANIM_EVENT``)를 메인 스레드에서 처리한다.

    - ``tbs_sim_screen`` 으로 보조 USD 컨텍스트를 골라 **포트 LOT prim 가시성**을 맞춘다.
    - ``handle_sim_event_for_animation`` → rules/map → JSON ``SequenceRunner``(화면별 USD 컨텍스트).
    """
    p = payload if isinstance(payload, dict) else {}
    # stop/reset 이후 들어온 잔여 이벤트는 무시한다.
    try:
        gen_now = int(getattr(ext, "_sim_run_gen", 0) or 0)
        gen_evt = int(str(p.get("_run_gen", gen_now) or gen_now).strip() or gen_now)
        if gen_evt != gen_now:
            return
    except Exception:
        pass
    try:
        scr = int(str(p.get("tbs_sim_screen", "1") or "1").strip() or "1")
    except Exception:
        scr = 1
    occ = p.get("ports_occupancy", {})
    if not isinstance(occ, dict):
        occ = {}
    # 안정성:
    # - ports_occupancy가 빈 dict이거나(누락), 부분 dict(키 누락) 또는 전부 빈 값이면
    #   포트상태 패널이 통째로 비어 보이거나 "전 포트 EMPTY"로 덮이는 현상이 생길 수 있다.
    # - 따라서 마지막 스냅샷으로 폴백/merge하고, 빈 값으로는 last snapshot을 덮어쓰지 않는다.
    _REQ_PORT_KEYS = ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")
    try:
        by_prev = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
        if not isinstance(by_prev, dict):
            by_prev = {}
            ext._sim_last_ports_occupancy_by_screen = by_prev
        occ_prev = by_prev.get(str(scr))
    except Exception:
        by_prev = {}
        occ_prev = None
    # 1) 부분 dict는 merge (누락 키가 '-'로 보이는 문제 방지)
    try:
        if isinstance(occ_prev, dict) and occ_prev:
            if occ and any((k not in occ) for k in _REQ_PORT_KEYS):
                merged = dict(occ_prev)
                merged.update(dict(occ))
                occ = merged
    except Exception:
        pass
    # 2) 빈 dict는 마지막 스냅샷으로 폴백
    if not occ:
        try:
            if isinstance(occ_prev, dict) and occ_prev:
                occ = dict(occ_prev)
        except Exception:
            pass
    # 3) "전부 빈 값"도 마지막 스냅샷으로 폴백(명시적 reset payload가 아닌 이상)
    try:
        if occ and (not any(bool(str(v or "").strip()) for v in occ.values())):
            if isinstance(occ_prev, dict) and occ_prev and any(bool(str(v or "").strip()) for v in occ_prev.values()):
                occ = dict(occ_prev)
    except Exception:
        pass
    # 마지막 점유 스냅샷 저장(빈/무의미한 스냅샷은 저장하지 않음)
    try:
        if isinstance(by_prev, dict) and occ and any((k in occ) for k in _REQ_PORT_KEYS):
            by_prev[str(scr)] = dict(occ)
    except Exception:
        pass
    ctx_nm = _usd_context_name_for_sim_screen(ext, scr)
    try:
        apply_port_lot_prim_visibility_for_context(ctx_nm, occ)
    except Exception:
        try:
            apply_port_lot_prim_visibility(occ)
        except Exception:
            pass
    _update_port_occupancy_panel(ext, occ, str(p.get("sim_time", "")), screen=scr)
    # 포트상태 갱신 전용 이벤트: 목록에 없는 내부 이벤트이므로 애니/공정확인창을 띄우지 않는다.
    try:
        if str(p.get("seq", "") or "").strip().upper() == "PORT_OCC_REFRESH":
            return
    except Exception:
        pass
    # 포트상태 좌/우 점(●) 카운터:
    # - READYTOLOAD 발생 시(생성 이벤트) 좌측 초록 ● +1
    # - READYTOUNLOAD 발생 시(회수 요청) 우측 빨강 ● +1
    # - 실제 감소는 애니 완료 시점(ARRIVED(OHT->*) 완료 / REMOVED 완료)에서 수행
    # (요청으로 제거) 포트상태 좌/우 점 표시 기능 비활성화
    verbose = panel_mode != SimLogPanelMode.PROGRESS_ONLY
    # 화면별 보조 USD 컨텍스트에서도 MOVE 등 JSON 시퀀스가 대상 스테이지에 적용되도록
    # `_execute_mapped_sequence_stub` → `SequenceRunner.run(usd_context_name=...)` 경로를 탄다.
    handle_sim_event_for_animation(ext, p, verbose=verbose)
    # 한 화면 이벤트 처리 중 UI 큐에 다른 화면 timeline_only 가 밀릴 수 있어, 엔진 시각으로 전 열 EP 막대 동기화
    try:
        _sync_all_ep_occ_timelines_from_engines(ext)
    except Exception:
        pass


def _sim_ui_sink_history_line(ext: Any, line: str, panel_mode: SimLogPanelMode) -> None:
    if not line:
        return
    if panel_mode == SimLogPanelMode.PROGRESS_ONLY:
        return
    _append_sim_log(ext, line)


def _finalize_sim_timeline_on_done(ext: Any) -> None:
    """
    시뮬레이션이 자연 종료(완료)되었을 때, EP 타임라인 막대그래프 진행을 즉시 종료 상태로 고정한다.

    목표(요구사항):
    - 종료 후에도 UI 가상 시간 ticker가 계속 돌아 막대가 진행되는 현상 방지
    - 요약 로그의 총 시간(=env.now)과 막대그래프의 총 길이(스케일)가 일치하도록 확정
    """
    # 1) UI 가상 시간 ticker 중단(종료 후 막대가 계속 전진하는 원인)
    try:
        sub = getattr(ext, "_sim_ep_timeline_ui_sub", None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        ext._sim_ep_timeline_ui_sub = None
    except Exception:
        pass

    # 2) 화면별 최종 sim_time(env.now)을 얻어 스케일/표시를 확정한다.
    final_by_screen: Dict[str, float] = {}
    try:
        engs = list(getattr(ext, "_sim_engines", None) or [])
    except Exception:
        engs = []
    if engs:
        for i, eng in enumerate(engs):
            if eng is None:
                continue
            scr_key = str(i + 1)
            try:
                t = float(getattr(getattr(eng, "env", None), "now", 0.0) or 0.0)
            except Exception:
                t = 0.0
            final_by_screen[scr_key] = max(0.0, t)
    else:
        eng = getattr(ext, "_sim_engine", None)
        if eng is not None:
            try:
                t = float(getattr(getattr(eng, "env", None), "now", 0.0) or 0.0)
            except Exception:
                t = 0.0
            final_by_screen["1"] = max(0.0, t)

    # 3) UI 그래프들이 사용하는 total_est / virtual_time / state를 최종값으로 덮어쓴다.
    try:
        by_te = getattr(ext, "_sim_last_total_est_by_screen", None)
        if not isinstance(by_te, dict):
            by_te = {}
            ext._sim_last_total_est_by_screen = by_te
        for scr_key, t in final_by_screen.items():
            if t > 0.0:
                by_te[str(scr_key)] = float(t)
    except Exception:
        pass
    try:
        vt_by = getattr(ext, "_sim_ep_timeline_virtual_time_by_screen", None)
        if not isinstance(vt_by, dict):
            vt_by = {}
            ext._sim_ep_timeline_virtual_time_by_screen = vt_by
        for scr_key, t in final_by_screen.items():
            vt_by[str(scr_key)] = float(t)
    except Exception:
        pass
    try:
        st_by = getattr(ext, "_sim_ep_occ_timeline_state_by_screen", None)
        if not isinstance(st_by, dict):
            st_by = {}
            ext._sim_ep_occ_timeline_state_by_screen = st_by
        for scr_key, t in final_by_screen.items():
            st = st_by.get(str(scr_key))
            if not isinstance(st, dict):
                continue
            # total_est_fixed는 포트상태 아래 타임라인의 "전체 길이(스케일)"로 사용된다.
            st["total_est_fixed"] = float(max(0.0, t))
            # 종료 시점 이후 추가 누적이 발생하지 않도록 t_last도 최종값으로 맞춘다.
            st["t_last"] = float(max(0.0, t))
    except Exception:
        pass

    # 4) 즉시 1회 재렌더(최종 시각/스케일 반영)
    chans = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans, list) and chans:
        last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
        for ch in chans:
            if not isinstance(ch, dict):
                continue
            try:
                scr_key = str(int(ch.get("screen", 1) or 1))
            except Exception:
                scr_key = "1"
            t_final = float(final_by_screen.get(scr_key, final_by_screen.get("1", 0.0)) or 0.0)
            occ = None
            try:
                occ = last_by.get(str(scr_key)) if isinstance(last_by, dict) else None
            except Exception:
                occ = None
            if isinstance(occ, dict):
                try:
                    _update_ep_timeline_under_port_state(ext, ch, occ, f"{t_final:.2f}")
                except Exception:
                    pass
                try:
                    # 진행현황 패널 하단 EP 타임라인도 최종 total로 맞춘다.
                    payload = {
                        "sim_time": f"{t_final:.2f}",
                        "sim_total_est_sec": f"{t_final:.2f}",
                        "ep_ports": [k for k in ("EP1", "EP2", "EP3") if k in occ],
                        "ep_occ": {k: ("EMPTY" if not str(occ.get(k, "") or "").strip() else "FULL") for k in ("EP1", "EP2", "EP3")},
                        "all_ep_empty": "1" if all(not str(occ.get(k, "") or "").strip() for k in ("EP1", "EP2", "EP3") if k in occ) else "0",
                    }
                    _update_progress_ep_timeline_widget(ext, ch, payload)
                except Exception:
                    pass


def _sim_ui_sink_action(ext: Any, payload: Any) -> None:
    if str(payload) == SimUiControlAction.EXPORT_XLSX.value:
        _finalize_sim_timeline_on_done(ext)
        _export_sim_logs_to_xlsx(ext)


def _sim_ui_sink_gate(ext: Any, payload: Dict[str, Any]) -> None:
    _show_sim_gate_dialog(ext, payload if isinstance(payload, dict) else {})


def _coerce_sim_ui_queue_kind(kind: Any) -> str:
    """
    큐에서 꺼낸 kind가 SimUiQueueKind 멤버일 때 str(kind)는 'SimUiQueueKind.GATE'처럼
    값이 아니라 멤버 이름이 되어 라우팅이 깨진다. 항상 실제 큐 문자열 값으로 맞춘다.
    """
    if isinstance(kind, SimUiQueueKind):
        return kind.value
    return str(kind)


def _dispatch_sim_ui_queue_item(ext: Any, kind: str, payload: Any, panel_mode: SimLogPanelMode) -> None:
    if kind == SimUiQueueKind.PROGRESS.value:
        _sim_ui_sink_progress(ext, payload if isinstance(payload, dict) else {})
    elif kind == SimUiQueueKind.ANIM_EVENT.value:
        _sim_ui_sink_anim_event(ext, payload if isinstance(payload, dict) else {}, panel_mode)
    elif kind == SimUiQueueKind.ACTION.value:
        _sim_ui_sink_action(ext, payload)
    elif kind == SimUiQueueKind.GATE.value:
        _sim_ui_sink_gate(ext, payload if isinstance(payload, dict) else {})
    elif kind == SimUiQueueKind.HISTORY_LINE.value:
        line = payload if isinstance(payload, str) else str(payload)
        _sim_ui_sink_history_line(ext, line, panel_mode)
    else:
        line = payload if isinstance(payload, str) else str(payload)
        _sim_ui_sink_history_line(ext, line, panel_mode)


def _build_prerun_timetable_text(results_by_screen: Any) -> Dict[int, str]:
    """
    프리런 결과(SimPreRunResult.items)를 **JSON 라인 형식의 타임테이블**로 만든다.

    출력 정책(요구사항):
    - 한 줄에 한 JSON 객체. 각 줄은 두 종류 중 하나.
        ┌─ kind="event": 시뮬 이벤트 발생 시점(ARRIVED/MOVE_*/REMOVED/FOUP_PROCESS_START/END 등)
        └─ kind="step":  공정/애니 동작 시작 시점(progress 의 RUNNING 첫 emit, elapsed=0.0)
    - 같은 시각이면 event → step 순서로 정렬.
    - port_id 등은 문자열("EP1", "BP3" 등)로 그대로 유지(시뮬 내부 표기와 일치).
    - 동작 라인(step)에는 anim 파일명/공정시간/애니시간/동작 설명/공정시간우선 등을 함께 동봉.

    출력 헤더는 ``"[SIM] 타임테이블(프리런) — 화면N"`` 으로 두어, ``_append_sim_log`` 의
    "타임테이블만 표시" 필터(timetable_only)를 그대로 통과한다.
    """
    out: Dict[int, str] = {}
    if not isinstance(results_by_screen, dict):
        return out

    def _f(x: Any, d: float = 0.0) -> float:
        try:
            return float(str(x).strip() or d)
        except Exception:
            return float(d)

    def _s(v: Any) -> str:
        try:
            return str(v).strip() if v is not None else ""
        except Exception:
            return ""

    for scr, res in results_by_screen.items():
        try:
            si = int(scr)
        except Exception:
            continue
        items = getattr(res, "items", None)
        if not isinstance(items, (list, tuple)):
            continue

        rows: List[Dict[str, Any]] = []
        for it in items:
            try:
                kind = str(getattr(it, "kind", "") or "").strip().lower()
                p = getattr(it, "payload", None)
                t_val = round(_f(getattr(it, "t", 0.0), 0.0), 2)

                # 1) 시뮬 이벤트 라인 (kind="event")
                if kind == "event" and isinstance(p, dict):
                    seq = _s(p.get("seq")).upper()
                    if not seq:
                        continue
                    row: Dict[str, Any] = {
                        "t": t_val,
                        "screen": si,
                        "kind": "event",
                        "event": seq,
                    }
                    # 있으면 동봉(없으면 키 자체 생략 → JSON 한 줄을 깔끔하게)
                    for k in ("port_id", "from_port_id", "to_port_id", "lot_id", "foup_id", "lot_seq"):
                        v = _s(p.get(k))
                        if v:
                            row[k] = v
                    rows.append(row)

                # 2) 동작 시작 라인 (kind="step") = progress.RUNNING 첫 emit (elapsed=0.0)
                elif kind == "progress" and isinstance(p, dict):
                    st = _s(p.get("status")).upper()
                    el = _f(p.get("elapsed", 0.0), 0.0)
                    if st != "RUNNING" or abs(el) > 1e-9:
                        continue
                    ev = _s(p.get("event_seq") or p.get("sequence_name")).upper()
                    if not ev:
                        continue
                    row = {
                        "t": t_val,
                        "screen": si,
                        "kind": "step",
                        "event": ev,
                    }
                    pid = _s(p.get("port_id"))
                    if pid:
                        row["port_id"] = pid
                    label = _s(p.get("label"))
                    if label:
                        row["label"] = label
                    # anim 파일명: 비어 있어도 명시적으로 빈 문자열로 둔다(필드 존재 자체가 의미)
                    row["anim"] = _s(p.get("linked_anim_json"))
                    row["proc_sec"] = round(_f(p.get("proc_sec", 0.0), 0.0), 2)
                    row["anim_sec"] = round(_f(p.get("anim_sec", 0.0), 0.0), 2)
                    detail = _s(p.get("detail"))
                    if detail:
                        row["detail"] = detail
                    ptp = _s(p.get("process_time_priority"))
                    if ptp:
                        row["process_time_priority"] = ptp
                    rows.append(row)
            except Exception:
                continue

        if not rows:
            continue

        # 같은 시각이면 event 를 먼저, step 을 다음에
        kind_prio = {"event": 0, "step": 1}
        try:
            rows.sort(key=lambda r: (
                float(r.get("t", 0.0)),
                int(kind_prio.get(str(r.get("kind", "")), 9)),
            ))
        except Exception:
            pass

        lines: List[str] = []
        lines.append(f"[SIM] 타임테이블(프리런) — 화면{si}")
        for r in rows:
            try:
                lines.append(json.dumps(r, ensure_ascii=False))
            except Exception:
                continue
        out[si] = "\n".join(lines).strip()
    return out


def _drain_sim_log_queue(ext: Any) -> None:
    try:
        # 프리런 완료 시점에 타임라인 플레이어를 시작한다(메인 스레드에서만).
        try:
            ev = getattr(ext, "_sim_prerun_done_evt", None)
            started = bool(getattr(ext, "_sim_playback_started", False))
            if (not started) and ev is not None and hasattr(ev, "is_set") and ev.is_set():
                results = getattr(ext, "_sim_prerun_results_by_screen", None)
                if isinstance(results, dict) and results:
                    try:
                        ext._sim_playback_started = True
                    except Exception:
                        pass

                    # 화면별 총 시뮬 시간 확정(막대 스케일/표시)
                    try:
                        by = getattr(ext, "_sim_last_total_est_by_screen", None)
                        if not isinstance(by, dict):
                            by = {}
                            ext._sim_last_total_est_by_screen = by
                        for scr, res in results.items():
                            try:
                                by[str(int(scr))] = float(res.final_sim_time)
                            except Exception:
                                continue
                    except Exception:
                        pass

                    # 프리런 타임테이블을 [SIM] 창(이력) + 콘솔에 출력
                    try:
                        printed = bool(getattr(ext, "_sim_prerun_timetable_printed", False))
                    except Exception:
                        printed = False
                    if not printed:
                        try:
                            ext._sim_prerun_timetable_printed = True
                        except Exception:
                            pass
                        try:
                            tb_by = _build_prerun_timetable_text(results)
                        except Exception:
                            tb_by = {}
                        if isinstance(tb_by, dict) and tb_by:
                            for si, txt in tb_by.items():
                                if not str(txt or "").strip():
                                    continue
                                try:
                                    # UI [SIM] 창(화면별 history_label)에 블록 형태로 붙인다.
                                    _append_sim_log_channel(ext, int(si), str(txt))
                                except Exception:
                                    pass
                                # 콘솔에도 동일 블록 출력(화면별 구분)
                                try:
                                    print(str(txt), flush=True)
                                except Exception:
                                    pass
                        else:
                            try:
                                _append_sim_log(ext, "[SIM] 타임테이블 생성: 표시할 DONE progress 항목이 없습니다.")
                            except Exception:
                                pass

                    # UI 그래프 동기화용 playback 엔진 생성
                    playback_engs: List[Any] = []
                    try:
                        max_scr = max(int(s) for s in results.keys())
                    except Exception:
                        max_scr = 1
                    for i in range(1, max_scr + 1):
                        rr = results.get(int(i))
                        if rr is None:
                            continue
                        playback_engs.append(PlaybackEngine(final_sim_time=float(rr.final_sim_time)))
                    try:
                        ext._sim_engines = playback_engs
                        ext._sim_engine = playback_engs[0] if playback_engs else None
                    except Exception:
                        pass

                    def _emit(kind: str, payload: Any, screen: int) -> None:
                        # 재생 중에는 payload에 실제 총 시간을 주입해 진행/막대 스케일을 확정한다.
                        if isinstance(payload, dict):
                            try:
                                rr2 = results.get(int(screen))
                                if rr2 is not None:
                                    payload = dict(payload)
                                    payload["sim_total_est_sec"] = f"{float(rr2.final_sim_time):.2f}"
                            except Exception:
                                pass
                        if kind == "log":
                            line = payload if isinstance(payload, str) else str(payload)
                            if int(screen) > 1:
                                line = f"[화면{int(screen)}] {line}"
                            post_sim_history_line(ext, line)
                        elif kind == "event":
                            if isinstance(payload, dict):
                                post_sim_anim_event(ext, payload)
                        elif kind == "progress":
                            if isinstance(payload, dict):
                                post_sim_progress_update(ext, payload)

                    def _speed() -> float:
                        try:
                            m = getattr(ext, "_sim_speed_model", None)
                            return float(m.get_value_as_float()) if m is not None else 1.0
                        except Exception:
                            return 1.0

                    player = SimTimelinePlayer(results_by_screen=results, emit_fn=_emit, speed_supplier=_speed)
                    player.start()
                    try:
                        ext._sim_playback_player = player
                    except Exception:
                        pass
                    # 첫 공정(첫 이벤트) 전에도 진행현황/시간이 계속 증가하도록 초기 payload를 즉시 세팅한다.
                    try:
                        for scr, rr in results.items():
                            try:
                                scr_i = int(scr)
                            except Exception:
                                scr_i = 1
                            p0 = {
                                "tbs_sim_screen": str(scr_i),
                                "sim_time": "0.00",
                                "sim_total_est_sec": f"{float(rr.final_sim_time):.2f}",
                                "label": "대기",
                                "detail": "",
                                "status": "RUNNING",
                                "elapsed": "0.0",
                                "total": "0.0",
                                "percent": "0",
                            }
                            _update_sim_progress(ext, p0)
                    except Exception:
                        pass

                    # UI update stream에서 재생 tick
                    try:
                        sub = getattr(ext, "_sim_playback_ui_sub", None)
                        if sub is not None:
                            try:
                                sub.unsubscribe()
                            except Exception:
                                pass
                        ext._sim_playback_ui_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
                            lambda _e: _tick_playback(ext),
                            name="morph.tbs_control_2:sim_playback_tick",
                        )
                    except Exception:
                        pass
                    try:
                        _append_sim_log(ext, "[SIM] 프리런 완료: 결과 타임라인을 재생합니다.")
                    except Exception:
                        pass
        except Exception:
            pass

        q = getattr(ext, "_sim_log_queue", None)
        if q is None:
            return

        # 공정설정 시간 우선 모드에서의 애니 중단은 tick 스레드가 아니라 UI(메인) 스레드에서만 수행한다.
        # tick 스레드에서 stop_all_* / runner.stop() 등을 호출하면 Kit 내부가 스레드-unsafe로 크래시할 수 있다.
        try:
            # 화면별 interrupt 우선 처리
            by = getattr(ext, "_sim_interrupt_anim_event_by_screen", None)
            fn_by = getattr(ext, "_sim_interrupt_anim_apply_fn_by_screen", None)
            if isinstance(by, dict) and isinstance(fn_by, dict):
                for scr, ev in list(by.items()):
                    try:
                        if ev is None or not hasattr(ev, "is_set") or not ev.is_set():
                            continue
                        try:
                            ev.clear()
                        except Exception:
                            pass
                        fn = fn_by.get(str(scr))
                        if callable(fn):
                            try:
                                fn()
                            except Exception:
                                pass
                    except Exception:
                        continue
        except Exception:
            pass
        try:
            ie = getattr(ext, "_sim_interrupt_anim_event", None)
            if ie is not None and hasattr(ie, "is_set") and ie.is_set():
                try:
                    ie.clear()
                except Exception:
                    pass
                fn = getattr(ext, "_sim_interrupt_anim_apply_fn", None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass
        except Exception:
            pass

        # 표시모드 제거: 항상 둘다(진행현황+이력로그)
        panel_mode = SimLogPanelMode.ALL
        count = 0
        # 중요: UI 프레임 1회당 처리량 상한.
        # 큐가 많아도 렌더링 starvation을 막기 위해 200개까지만 드레인한다.
        while count < 200:
            try:
                item = q.get_nowait()
            except Exception:
                break
            kind, payload = (
                item if isinstance(item, tuple) and len(item) == 2 else (SimUiQueueKind.HISTORY_LINE.value, item)
            )
            _dispatch_sim_ui_queue_item(ext, _coerce_sim_ui_queue_kind(kind), payload, panel_mode)
            count += 1

            # 공정확인 체크 + gate pause 상태면, "확인창 1개를 띄울 때까지만" 처리하고 멈춘다.
            # (gate pause를 너무 이르게 걸어도 UI가 1개 이벤트를 처리해 창을 띄울 수 있어야 한다)
            try:
                confirm_each = bool(
                    getattr(ext, "_sim_confirm_each_step_model", None) is not None
                    and ext._sim_confirm_each_step_model.get_value_as_bool()
                )
            except Exception:
                confirm_each = False
            if confirm_each:
                try:
                    gp = getattr(ext, "_sim_gate_pause_event", None)
                    if gp is not None and gp.is_set() and getattr(ext, "_sim_gate_dialog", None) is not None:
                        break
                except Exception:
                    break
    except Exception as e:
        # UI 드레인 예외가 발생해도 구독이 끊기지 않도록 보호
        print(f"[SIM UI] 로그 드레인 예외: {e}", flush=True)


def _tick_playback(ext: Any) -> None:
    """프리런 타임라인 플레이어 1프레임 tick + env.now 동기화."""
    try:
        player = getattr(ext, "_sim_playback_player", None)
        if player is None:
            return
        if not getattr(player, "is_playing", lambda: False)():
            return
        player.tick()
        # env.now는 UI 막대 동기화에 사용되므로 화면별로 업데이트한다.
        engs = getattr(ext, "_sim_engines", None)
        results = getattr(ext, "_sim_prerun_results_by_screen", None)
        if isinstance(engs, list):
            # 막대가 끊기지 않도록 timeline_only progress를 일정 주기로 합성 emit한다.
            try:
                hb = getattr(ext, "_sim_playback_timeline_hb", None)
                if not isinstance(hb, dict):
                    hb = {}
                    ext._sim_playback_timeline_hb = hb
                now_wall = time.perf_counter()
            except Exception:
                hb = {}
                now_wall = time.perf_counter()
            for i, eng in enumerate(engs):
                if eng is None:
                    continue
                scr = i + 1
                try:
                    tnow = float(player.sim_now(scr))
                except Exception:
                    tnow = 0.0
                try:
                    if hasattr(eng, "_set_now"):
                        eng._set_now(tnow)  # type: ignore[attr-defined]
                    elif hasattr(eng, "env") and eng.env is not None:
                        eng.env.now = float(tnow)  # type: ignore[attr-defined]
                except Exception:
                    pass
                # 10Hz emit (screen별)
                try:
                    last = float(hb.get(str(scr), 0.0) or 0.0)
                except Exception:
                    last = 0.0
                if (now_wall - last) >= 0.10:
                    try:
                        hb[str(scr)] = float(now_wall)
                    except Exception:
                        pass
                    # last ports snapshot 기반으로 EP 타임라인만 전진(timeline_only)
                    try:
                        te = None
                        if isinstance(results, dict) and results.get(int(scr)) is not None:
                            te = float(results[int(scr)].final_sim_time)
                        payload = {
                            "tbs_sim_screen": str(scr),
                            "sim_time": f"{float(tnow):.6f}",
                            "timeline_only": "1",
                            "label": "EP 타임라인",
                            "detail": "",
                            "status": "RUNNING",
                            "elapsed": "0.0",
                            "total": "0.0",
                            "percent": "0",
                        }
                        if isinstance(te, (float, int)) and float(te) > 0.0:
                            payload["sim_total_est_sec"] = f"{float(te):.2f}"
                        post_sim_progress_update(ext, payload)
                    except Exception:
                        pass

                # 단계완료(DONE) 상태에서도 t(sim)이 끊기지 않도록,
                # 마지막 진행현황 payload를 복제해 sim_time만 주기적으로 갱신한다(텍스트 업데이트).
                try:
                    last2 = float(hb.get(f"prog_{scr}", 0.0) or 0.0)
                except Exception:
                    last2 = 0.0
                if (now_wall - last2) >= 0.20:
                    try:
                        hb[f"prog_{scr}"] = float(now_wall)
                    except Exception:
                        pass
                    try:
                        by_lp = getattr(ext, "_sim_progress_last_payload_by_screen", None)
                        lp = by_lp.get(str(scr)) if isinstance(by_lp, dict) else None
                        # 첫 공정(첫 progress) 전에도 t(sim)이 계속 증가해야 한다.
                        # lp(마지막 progress payload)가 없으면 "대기" 기본 payload를 합성한다.
                        if isinstance(lp, dict) and str(lp.get("label", "") or "").strip():
                            p3 = dict(lp)
                        else:
                            p3 = {
                                "tbs_sim_screen": str(scr),
                                "label": "대기",
                                "detail": "",
                                "status": "RUNNING",
                                "elapsed": "0.0",
                                "total": "0.0",
                                "percent": "0",
                            }
                        p3["tbs_sim_screen"] = str(scr)
                        p3["sim_time"] = f"{float(tnow):.2f}"
                        # 총시간은 유지/확정
                        try:
                            if isinstance(results, dict) and results.get(int(scr)) is not None:
                                p3["sim_total_est_sec"] = f"{float(results[int(scr)].final_sim_time):.2f}"
                        except Exception:
                            pass
                        post_sim_progress_update(ext, p3)
                    except Exception:
                        pass

            # 전체 종료 처리(마지막 공정 RUNNING 멈춤 방지)
            try:
                if isinstance(results, dict) and results:
                    done_all = True
                    for scr_k, res in results.items():
                        try:
                            if float(player.sim_now(int(scr_k))) < float(res.final_sim_time) - 1e-6:
                                done_all = False
                                break
                        except Exception:
                            done_all = False
                            break
                    if done_all and (not bool(getattr(ext, "_sim_playback_done", False))):
                        ext._sim_playback_done = True
                        # 그래프/스케일 종료 고정
                        try:
                            _finalize_sim_timeline_on_done(ext)
                        except Exception:
                            pass
                        # 화면별 DONE 진행현황 1회 emit
                        for scr_k, res in results.items():
                            try:
                                scr_i = int(scr_k)
                            except Exception:
                                scr_i = 1
                            try:
                                p_done = {
                                    "tbs_sim_screen": str(scr_i),
                                    "sim_time": f"{float(res.final_sim_time):.2f}",
                                    "sim_total_est_sec": f"{float(res.final_sim_time):.2f}",
                                    "label": "완료",
                                    "detail": "",
                                    "status": "DONE",
                                    "elapsed": f"{float(res.final_sim_time):.1f}",
                                    "total": f"{float(res.final_sim_time):.1f}",
                                    "percent": "100",
                                }
                                post_sim_progress_update(ext, p_done)
                            except Exception:
                                pass
                        try:
                            _enqueue_control_action(ext, SimUiControlAction.EXPORT_XLSX.value)
                        except Exception:
                            pass
                        # DONE 이후에는 heartbeat(progress 합성)가 계속 돌 필요가 없다.
                        # player를 stop 처리해 _tick_playback()이 더 이상 PROGRESS를 enqueue하지 않게 한다.
                        try:
                            if hasattr(player, "stop"):
                                player.stop()
                        except Exception:
                            pass
            except Exception:
                pass
    except Exception:
        pass


def _sim_active_anim_owner_screen(ext: Any) -> int:
    """
    현재(또는 직전) JSON 애니 job 이 어느 시뮼 화면에서 시작됐는지 1-based 인덱스로 반환한다.

    ``_execute_mapped_sequence_stub`` 가 job 에 넣은 ``tbs_sim_screen`` 과 ``_sim_anim_active`` 를 읽는다.
    값이 없거나 파싱 실패 시 1(메인).
    """
    active = getattr(ext, "_sim_anim_active", None) or {}
    if isinstance(active, dict):
        try:
            v = int(str(active.get("tbs_sim_screen", "1") or "1").strip() or "1")
            return max(1, v)
        except Exception:
            pass
    return 1


def _ensure_tick_pause_map_for_multi(ext: Any, n_ch: int) -> None:
    """분할 N>1 시 화면마다 독립 Event — 없으면 전역 pause 로 떨어져 전 엔진 틱이 같이 멈출 수 있다."""
    if n_ch <= 1:
        return
    try:
        m: Dict[str, threading.Event] = {}
        for i in range(1, n_ch + 1):
            m[str(i)] = threading.Event()
        ext._sim_tick_pause_events_by_screen = m
        ub: Dict[str, Any] = {str(i): None for i in range(1, n_ch + 1)}
        ext._sim_tick_pause_until_wall_by_screen = ub
    except Exception:
        pass


def _is_multi_viewport_sim(ext: Any) -> bool:
    try:
        nsp = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
        return nsp > 1
    except Exception:
        return False


def _pause_event_for_screen(ext: Any, screen_idx: int) -> Optional[threading.Event]:
    try:
        m = getattr(ext, "_sim_tick_pause_events_by_screen", None)
        if not isinstance(m, dict):
            return getattr(ext, "_sim_tick_pause_event", None)
        key = str(max(1, int(screen_idx)))
        ev = m.get(key)
        if ev is None:
            ev = threading.Event()
            m[key] = ev
        return ev
    except Exception:
        return getattr(ext, "_sim_tick_pause_event", None)


def _multi_tick_should_skip_for_screen(ext: Any, screen_idx: int, anim_running: bool) -> bool:
    """
    멀티 뷰 전용: ``_sim_tick_pause_event`` 가 켜져 있을 때 이 화면만 tick 을 건너뛸지.

    - SequenceRunner 가 돌면: 해당 애니가 시작된 화면(owner)만 sim 을 잠시 멈춘다(배속>1 동기).
    - Kit translate/rotate/curve 만 돌면: 메인 스테이지(화면 1)만 멈춘다.
    - 그 외 pause 구간(보수적): 모든 화면 멈춤(기존 단일 루프와 동일).
    """
    pause_evt = getattr(ext, "_sim_tick_pause_event", None)
    if pause_evt is None or not pause_evt.is_set():
        return False
    owner = _sim_active_anim_owner_screen(ext)
    try:
        ru = bool(
            getattr(ext, "_sim_runner", None) is not None
            and getattr(ext._sim_runner, "is_running", lambda: False)()
        )
    except Exception:
        ru = False
    if ru:
        return screen_idx == owner
    kit_only = bool(anim_running) and not ru
    if kit_only:
        return screen_idx == 1
    if anim_running:
        return True
    uw = getattr(ext, "_sim_tick_pause_until_wall", None)
    if isinstance(uw, (float, int)) and time.monotonic() < float(uw):
        return screen_idx == owner
    return True


def _sim_multi_engine_tick_worker(
    ext: Any,
    sim: Any,
    screen_idx: int,
    stop_evt: threading.Event,
    export_lock: threading.Lock,
) -> None:
    """
    한 개 ``TBSSimulationEngine`` 전용 tick 루프(별도 스레드에서 실행).

    - ``screen_idx`` 에 해당하는 엔진만 ``sim.tick(scaled)``; 배속은 매 루프 ``_sim_speed_model`` 에서 읽는다.
    - ``_sim_tick_pause_event`` 가 켜져 있으면 ``_multi_tick_should_skip_for_screen`` 으로 **이 화면만**
      tick 을 건너뛸지 결정해, 다른 화면은 계속 진행한다.
    - ``ext._sim_engines`` 전원 ``is_done`` 이면 export 액션을 한 번만 enqueue 하고 ``_sim_multi_tick_shutdown`` 을 세운다.
    """
    last = time.perf_counter()
    printed = False
    while not stop_evt.is_set():
        if getattr(ext, "_sim_multi_tick_shutdown", False):
            break
        pause_evt = _pause_event_for_screen(ext, screen_idx)
        gate_pause_evt = getattr(ext, "_sim_gate_pause_event", None)
        try:
            confirm_each = bool(
                getattr(ext, "_sim_confirm_each_step_model", None) is not None
                and ext._sim_confirm_each_step_model.get_value_as_bool()
            )
        except Exception:
            confirm_each = False
        if not confirm_each and gate_pause_evt is not None and gate_pause_evt.is_set():
            try:
                gate_pause_evt.clear()
            except Exception:
                pass
        # 화면별 pause: 분할 N>1 에서는 애니 동기용 tick 정지를 쓰지 않는다(각 엔진 sim 시간이 끊기지 않게).
        multi_vp = _is_multi_viewport_sim(ext)
        if (not multi_vp) and pause_evt is not None and pause_evt.is_set():
            # fail-safe 1) 예상 애니 길이(벽시계) 경과 시 자동 해제
            try:
                until_by = getattr(ext, "_sim_tick_pause_until_wall_by_screen", None)
                until_t = until_by.get(str(screen_idx)) if isinstance(until_by, dict) else None
                if isinstance(until_t, (float, int)) and time.monotonic() >= float(until_t):
                    try:
                        pause_evt.clear()
                    except Exception:
                        pass
            except Exception:
                pass
            # fail-safe 2) runner/active가 없는데 pause만 남아있으면 해제
            try:
                runner_alive = False
                runners_by = getattr(ext, "_sim_runners_by_screen", None)
                rr = runners_by.get(str(screen_idx)) if isinstance(runners_by, dict) else None
                runner_alive = bool(rr is not None and getattr(rr, "is_running", lambda: False)())
            except Exception:
                runner_alive = False
            try:
                active_by = getattr(ext, "_sim_anim_active_by_screen", None)
                act = active_by.get(str(screen_idx)) if isinstance(active_by, dict) else None
                active_has = bool(isinstance(act, dict) and act)
            except Exception:
                active_has = False
            if (not runner_alive) and (not active_has):
                try:
                    pause_evt.clear()
                except Exception:
                    pass
            time.sleep(0.02)
            if pause_evt.is_set():
                continue

        now = time.perf_counter()
        dt = now - last
        last = now
        dt = max(0.001, min(dt, 0.1))
        try:
            sp = max(0.1, float(ext._sim_speed_model.get_value_as_float()))
        except Exception:
            sp = 1.0
        scaled = dt * sp
        try:
            if sim is not None and not getattr(sim, "is_done", False):
                sim.tick(scaled)
        except Exception:
            pass

        if not printed:
            try:
                print(f"[SIM] tick 동작 확인 (screen{screen_idx} worker)", flush=True)
            except Exception:
                pass
            printed = True

        eng_list = getattr(ext, "_sim_engines", None)
        if isinstance(eng_list, list) and len(eng_list) > 0:
            if all(getattr(s, "is_done", False) for s in eng_list if s is not None):
                try:
                    print("[SIM] 멀티 종료 감지", flush=True)
                except Exception:
                    pass
                with export_lock:
                    if not getattr(ext, "_sim_multi_export_done", False):
                        try:
                            ext._sim_multi_export_done = True
                        except Exception:
                            pass
                        try:
                            ext._sim_multi_tick_shutdown = True
                        except Exception:
                            pass
                        _enqueue_control_action(ext, SimUiControlAction.EXPORT_XLSX.value)
                break
        time.sleep(0.02)


def _sim_anim_status_key(ext: Any) -> Tuple[bool, str, int, str]:
    """진행 패널 중복 스킵용: 재생 여부·현재 파일·대기 큐·다음 파일."""
    runner = getattr(ext, "_sim_runner", None)
    try:
        running = bool(runner is not None and runner.is_running())
    except Exception:
        running = False
    active = getattr(ext, "_sim_anim_active", None) or {}
    cur_file = str(active.get("file", "") or "").strip() if isinstance(active, dict) else ""
    pend = getattr(ext, "_sim_anim_pending", None)
    plist = pend if isinstance(pend, list) else []
    q = len(plist)
    next_f = ""
    if plist and isinstance(plist[0], dict):
        next_f = str(plist[0].get("file", "") or "").strip()
    return (running, cur_file, q, next_f)


def _format_anim_status_footer(ext: Any) -> str:
    """진행현황 패널 하단: 현재 재생 JSON 파일·대기열.

    SequenceRunner.run()은 _begin_sequence()를 다음 프레임으로 미루므로,
    잠깐 동안은 _sim_anim_active.file 은 있는데 is_running() 이 False 인 구간이 있다.
    이 경우에도 파일명을 보여준다(진행현황에 '재생 없음'으로 보이는 문제 방지).
    시퀀스가 끝나면 _on_done 경로에서 _sim_anim_active 를 비운다.
    """
    running, cur_file, q, next_f = _sim_anim_status_key(ext)
    if cur_file:
        if running:
            lines = [f"애니메이션 파일(재생 중): {cur_file}"]
        else:
            lines = [f"애니메이션 파일: {cur_file}"]
        if q > 0 and next_f:
            lines.append(f"대기열: {q}건 (다음 {next_f})")
        return "\n".join(lines)
    if q > 0 and next_f:
        return "애니메이션: 대기 — 다음 " + next_f + (f" (큐 {q}건)" if q > 1 else "")
    return "애니메이션 파일: 재생 없음"


def _format_progress_anim_footer(ext: Any, payload: Dict[str, str]) -> str:
    """
    진행현황 하단: **현재 공정 단계**와 동일한 연계 JSON(엔진 ``linked_anim_json``)을 우선 표시하고,
    필요할 때만 시퀀스 러너 줄을 붙인다.

    러너의 ``_sim_anim_active`` 는 직전 시퀀스가 끝나기 전까지 이전 ``file`` 을 들고 있을 수 있어,
    공정은 ``REMOVED``(removed_ep2)인데 ``애니메이션 파일: arrived_ep1`` 처럼 **연계와 다른 이름**이 나오면 생략한다.
    대기 큐의 **다음** 항목이 연계 파일과 같으면 ``대기 — 다음 …`` 만 표시한다.
    """
    hint = str(payload.get("linked_anim_json") or "").strip()
    if not hint:
        return _format_anim_status_footer(ext)

    parts: List[str] = []
    rel = hint.replace("\\", "/")
    if "/" not in rel:
        rel = f"data/sim_sequences/{rel}"
    try:
        p = _normalize_json_path(rel)
        ex_lbl = "존재" if p.is_file() else "없음"
    except Exception:
        ex_lbl = "?"
    bn = Path(hint.replace("\\", "/")).name
    parts.append(f"이벤트 연계 JSON: {bn} ({ex_lbl})")

    hint_key = bn.lower()
    _, cur_file, q, next_f = _sim_anim_status_key(ext)
    cur_key = Path((cur_file or "").replace("\\", "/")).name.lower() if cur_file else ""
    next_key = Path((next_f or "").replace("\\", "/")).name.lower() if next_f else ""

    if cur_key == hint_key:
        runner = _format_anim_status_footer(ext).strip()
        if runner and runner != "애니메이션 파일: 재생 없음":
            parts.append(runner)
    elif q > 0 and next_key == hint_key:
        nf_disp = Path(next_f.replace("\\", "/")).name if next_f else ""
        parts.append("애니메이션: 대기 — 다음 " + nf_disp + (f" (큐 {q}건)" if q > 1 else ""))

    return "\n".join(parts)


def _refresh_sim_progress_from_last(ext: Any) -> None:
    """애니 시작/종료 직후 마지막 공정 진행 payload로 패널만 다시 그린다."""
    lp = getattr(ext, "_sim_progress_last_payload", None)
    if isinstance(lp, dict):
        _update_sim_progress(ext, lp)


def _update_sim_progress(ext: Any, payload: Dict[str, str]) -> None:
    """
    진행현황 텍스트를 갱신한다.

    - ``payload["tbs_sim_screen"]``(엔진 ``event_tags`` 병합)으로 **멀티 모니터** 중 해당 열의
      ``progress_label`` 에만 쓴다. 단일 모드는 첫 채널 + ``_sim_progress_text`` 레거시 모델.
    - RUNNING 일 때 동일 내용 반복 갱신을 줄이기 위해 ``_sim_progress_last_key`` 로 디듀프한다.
    """
    label = str(payload.get("label", "")).strip()
    # EP 타임라인 전용 업데이트는 텍스트를 덮어쓰지 않고 그래프만 갱신한다.
    try:
        if str(payload.get("timeline_only", "")).strip() in ("1", "true", "True", "ON", "on"):
            chans2 = getattr(ext, "_sim_monitor_channels", None)
            panel_slot = str(payload.get("tbs_sim_screen", "") or "1").strip() or "1"
            # 포트상태 아래 전용 EP 타임라인을 대기 구간에도 전진시키기 위해
            # 마지막 ports_occupancy 스냅샷을 사용한다.
            try:
                last_by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
                if not isinstance(last_by, dict):
                    last_by = {}
                    ext._sim_last_ports_occupancy_by_screen = last_by
                sk_occ = str(panel_slot or "1").strip() or "1"
                last_occ = last_by.get(sk_occ)
                if not isinstance(last_occ, dict):
                    # 시작 직후·첫 이벤트 전: 이벤트로 점유 스냅샷이 아직 없어도 EP 타임라인은 진행되어야 한다.
                    last_occ = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
                    last_by[sk_occ] = dict(last_occ)
            except Exception:
                last_occ = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
            if isinstance(chans2, list) and len(chans2) > 1:
                try:
                    pslot_i = int(str(panel_slot or "1").strip() or "1")
                except Exception:
                    pslot_i = 1
                pslot_i = max(1, min(len(chans2), pslot_i))
                chp = chans2[pslot_i - 1]
                if isinstance(chp, dict):
                    sim_t = str(payload.get("sim_time", ""))
                    _update_ep_timeline_under_port_state(ext, chp, last_occ, sim_t)
            elif isinstance(chans2, list) and len(chans2) == 1:
                chp0 = chans2[0]
                if isinstance(chp0, dict):
                    sim_t = str(payload.get("sim_time", ""))
                    _update_ep_timeline_under_port_state(ext, chp0, last_occ, sim_t)
            if isinstance(chans2, list) and len(chans2) > 1:
                try:
                    _sync_all_ep_occ_timelines_from_engines(ext)
                except Exception:
                    pass
            return
    except Exception:
        pass
    if not label:
        return
    status = str(payload.get("status", "RUNNING"))
    percent = str(payload.get("percent", "0"))
    elapsed = str(payload.get("elapsed", "0.0"))
    total = str(payload.get("total", "0.0"))
    sim_time = str(payload.get("sim_time", "0.00"))
    detail = str(payload.get("detail", ""))
    event_seq = str(payload.get("event_seq") or payload.get("sequence_name") or "").strip()
    linked_anim = str(payload.get("linked_anim_json") or "").strip()
    proc_sec = str(payload.get("proc_sec", "")).strip()
    anim_sec = str(payload.get("anim_sec", "")).strip()
    proc_pri = str(payload.get("process_time_priority", "")).strip()
    ep_occ = payload.get("ep_occ", {})
    all_ep_empty = str(payload.get("all_ep_empty", "")).strip()

    anim_key = _sim_anim_status_key(ext)
    try:
        panel_slot = str(payload.get("tbs_sim_screen", "") or "1").strip() or "1"
    except Exception:
        panel_slot = "1"

    # FOUP 공정 진행은 EP 포트별로 "줄을 다르게 고정"한 라벨에만 갱신한다.
    # - 단일 라벨에 다른 EP/단계 텍스트가 덮어써져 깜빡이는 문제를 근본적으로 차단.
    # - payload["port_id"] (또는 detail/label 의 EPn 패턴) 으로 라우팅한다.
    # - -Y 단계 DONE 수신 시 1.05초 뒤 해당 EP 라벨만 "대기" 로 되돌린다(다른 EP 라벨은 무관).
    try:
        if str(event_seq or "").strip().upper() == "FOUP_PROCESS":
            chansf = getattr(ext, "_sim_monitor_channels", None)
            if isinstance(chansf, list) and len(chansf) > 0:
                try:
                    si = int(str(panel_slot or "1").strip() or "1")
                except Exception:
                    si = 1
                si = max(1, min(len(chansf), si))
                chf = chansf[si - 1] if isinstance(chansf[si - 1], dict) else None
            else:
                chf = None
            # EP 식별자 결정: payload.port_id → label/detail 안의 EPn 패턴 폴백
            ep_id = ""
            try:
                ep_id = str(payload.get("port_id", "") or "").strip().upper()
            except Exception:
                ep_id = ""
            if not ep_id:
                try:
                    import re as _re
                    src_txt = (str(payload.get("label", "") or "") + " " + str(payload.get("detail", "") or "")).upper()
                    m = _re.search(r"\bEP(\d+)\b", src_txt)
                    if m:
                        n = int(m.group(1))
                        if 1 <= n <= 3:
                            ep_id = f"EP{n}"
                except Exception:
                    pass
            labels = (chf or {}).get("foup_progress_labels") if chf else None
            lbl = None
            if isinstance(labels, dict) and ep_id:
                lbl = labels.get(ep_id)
            if lbl is None:
                # 폴백: 구조가 갱신되기 전이거나 EP 식별 실패 시 단일 라벨에라도 표시(원래 동작 유지)
                lbl = (chf or {}).get("foup_progress_label") if chf else None
            if lbl is not None:
                t_sim = str(payload.get("sim_time", "0.00"))
                st = str(payload.get("status", "RUNNING"))
                pct = str(payload.get("percent", "0"))
                el = str(payload.get("elapsed", "0.0"))
                tot = str(payload.get("total", "0.0"))
                lab = str(payload.get("label", "") or "").strip()
                det = str(payload.get("detail", "") or "").strip()
                screen_num = int((chf or {}).get("screen", si) or si)
                head = (
                    f"{ep_id} FOUP 공정"
                    if (ep_id and screen_num == 1)
                    else (
                        f"{ep_id} FOUP 공정(화면{screen_num})"
                        if ep_id
                        else ("FOUP 공정" if screen_num == 1 else f"FOUP 공정(화면{screen_num})")
                    )
                )
                # 단계 표기(+Y/-Y/공정)는 detail 보다 label 이 더 짧고 깔끔
                stage = ""
                try:
                    if "+Y" in lab:
                        stage = "+Y"
                    elif "-Y" in lab:
                        stage = "-Y"
                    elif "공정" in lab or "공정" in det:
                        stage = "공정"
                except Exception:
                    stage = ""
                body = f"{head}: 진행 [{stage}]" if stage else f"{head}: 진행"
                lbl.text = f"{body} | {st} {pct}% ({el}/{tot}) | t={t_sim}"
                # 진행 중 색(노랑), DONE 색(연한 회녹색) 으로 구분
                try:
                    color = 0xFFFFE08A if str(st).upper() == "RUNNING" else 0xFF9FBFA0
                    lbl.style = {"color": color}
                except Exception:
                    pass
            # -Y(공정 종료 복귀) DONE 수신 시 해당 EP 만 1.05초 뒤 "대기" 로 되돌린다.
            try:
                lab_u = str(payload.get("label", "") or "").upper()
                if (
                    str(payload.get("status", "")).strip().upper() == "DONE"
                    and "-Y" in lab_u
                    and ep_id
                    and isinstance(labels, dict)
                    and labels.get(ep_id) is not None
                ):
                    _schedule_foup_label_reset(ext, si, ep_id, delay_sec=1.05)
            except Exception:
                pass
            return
    except Exception:
        pass
    # 화면별 마지막 진행현황 payload 저장(플레이백에서 DONE 상태에도 t(sim)을 부드럽게 갱신하기 위함)
    try:
        by_lp = getattr(ext, "_sim_progress_last_payload_by_screen", None)
        if not isinstance(by_lp, dict):
            by_lp = {}
            ext._sim_progress_last_payload_by_screen = by_lp
        by_lp[str(panel_slot)] = dict(payload)
    except Exception:
        pass
    dedupe_key = f"_panel_{panel_slot}"
    # total_est는 포트상태 아래 전용 그래프에서도 사용하므로 화면별로 저장
    try:
        pslot_g = str(payload.get("tbs_sim_screen", "") or "1").strip() or "1"
        try:
            te = float(str(payload.get("sim_total_est_sec", "")).strip() or "0.0")
        except Exception:
            te = 0.0
        if te > 0.0:
            by = getattr(ext, "_sim_last_total_est_by_screen", None)
            if not isinstance(by, dict):
                by = {}
                ext._sim_last_total_est_by_screen = by
            by[str(pslot_g)] = float(te)
    except Exception:
        pass

    if status == "RUNNING":
        try:
            last_key = getattr(ext, "_sim_progress_last_key", None)
            # 진행현황 디듀프 키에는 실제 표시 문자열에 영향을 주는 값들을 포함해야 한다.
            # (proc_sec/anim_sec/priority가 바뀌었는데도 elapsed/total이 같으면 UI가 갱신되지 않는 문제 방지)
            key = (
                panel_slot,
                # heartbeat(첫 공정 전/대기/DONE 포함)에서 sim_time만 바뀌는 업데이트도 표시되어야 한다.
                str(sim_time),
                str(percent),
                str(elapsed),
                str(total),
                str(status),
                label,
                event_seq,
                linked_anim,
                anim_key,
                proc_sec,
                anim_sec,
                proc_pri,
                # 총 시간(총=XXXs)은 header에 직접 반영되므로 키에 포함
                str(payload.get("sim_total_est_sec", "") or "").strip(),
            )
            if isinstance(last_key, dict) and last_key.get(dedupe_key) == key:
                return
            if isinstance(last_key, dict):
                last_key[dedupe_key] = key
        except Exception:
            pass
    else:
        try:
            last_key = getattr(ext, "_sim_progress_last_key", None)
            if isinstance(last_key, dict):
                last_key.pop(dedupe_key, None)
        except Exception:
            pass

    head = "[진행현황] 단계 완료" if status == "DONE" else "[진행현황] 진행 중"
    ev_line = f"이벤트: {event_seq}\n" if event_seq else ""
    anim_footer = _format_progress_anim_footer(ext, payload if isinstance(payload, dict) else {})
    sec_line = ""
    if proc_sec or anim_sec:
        pri_txt = "ON" if proc_pri in ("1", "true", "True", "ON", "on") else "OFF"
        sec_line = f"시간: 공정={proc_sec or '-'}s | 애니={anim_sec or '-'}s | 공정시간우선={pri_txt}\n"
    te_txt = ""
    try:
        te_txt = str(payload.get("sim_total_est_sec", "") or "").strip()
    except Exception:
        te_txt = ""
    total_head = f" | 총={te_txt}s" if te_txt else ""
    text = (
        f"{head}{total_head} | t(sim)={sim_time}s\n"
        f"{ev_line}"
        f"{label}\n"
        f"{sec_line}"
        f"진행률: {percent}% ({elapsed} / {total}s)\n"
        f"{detail}\n"
        f"---\n"
        f"{anim_footer}"
    )
    try:
        ext._sim_progress_last_payload = dict(payload)
    except Exception:
        ext._sim_progress_last_payload = payload
    try:
        chans2 = getattr(ext, "_sim_monitor_channels", None)
        if isinstance(chans2, list) and len(chans2) > 1:
            try:
                pslot_i = int(str(panel_slot or "1").strip() or "1")
            except Exception:
                pslot_i = 1
            pslot_i = max(1, min(len(chans2), pslot_i))
            chp = chans2[pslot_i - 1]
            if isinstance(chp, dict) and chp.get("progress_label") is not None:
                chp["progress_label"].text = text
                try:
                    _update_progress_ep_timeline_widget(ext, chp, payload if isinstance(payload, dict) else {})
                except Exception:
                    pass
        elif isinstance(chans2, list) and len(chans2) == 1:
            chp0 = chans2[0]
            if isinstance(chp0, dict) and chp0.get("progress_label") is not None:
                chp0["progress_label"].text = text
                try:
                    _update_progress_ep_timeline_widget(ext, chp0, payload if isinstance(payload, dict) else {})
                except Exception:
                    pass
    except Exception:
        pass
    try:
        chans3 = getattr(ext, "_sim_monitor_channels", None)
        if not (isinstance(chans3, list) and len(chans3) > 1) or str(panel_slot or "1").strip() in ("", "1"):
            ext._sim_progress_text.set_value(text)
    except Exception:
        try:
            ext._sim_progress_text.set_value(text)
        except Exception:
            pass
    if getattr(ext, "_sim_progress_label", None) is not None:
        ext._sim_progress_label.text = text


def _update_progress_ep_timeline_widget(ext: Any, ch: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """
    진행현황 패널 하단: EP 점유 상태를 시뮬 시간 기준으로 누적 막대그래프로 표현한다.
    - EP1/EP2(/EP3) + ALL_EP(모든 EP empty) 1줄씩
    - EMPTY=빨강, FULL=초록
    """
    host = ch.get("progress_ep_timeline_host")
    if host is None:
        return
    try:
        screen = int(ch.get("screen", 1))
    except Exception:
        screen = 1
    scr_key = str(screen)
    # 상태 저장소
    st_by = getattr(ext, "_sim_ep_timeline_state_by_screen", None)
    if not isinstance(st_by, dict):
        st_by = {}
        ext._sim_ep_timeline_state_by_screen = st_by
    st = st_by.get(scr_key)
    if not isinstance(st, dict):
        st = {"t_last": None, "rows": {}}
        st_by[scr_key] = st

    sim_time = None
    try:
        sim_time = float(str(payload.get("sim_time", "")).strip() or "0.0")
    except Exception:
        sim_time = None
    if sim_time is None:
        return
    t_last = st.get("t_last", None)
    st["t_last"] = sim_time
    if t_last is None:
        return
    dt = max(0.0, float(sim_time) - float(t_last))
    if dt <= 1e-9:
        return

    ep_occ = payload.get("ep_occ", {})
    # 일부 경로에서 dict가 문자열로 들어올 수 있어(예: "{'EP1': 'EMPTY'}") 보정한다.
    if not isinstance(ep_occ, dict):
        if isinstance(ep_occ, str) and ep_occ.strip().startswith("{"):
            try:
                import ast

                v = ast.literal_eval(ep_occ)
                ep_occ = v if isinstance(v, dict) else {}
            except Exception:
                ep_occ = {}
        else:
            ep_occ = {}
    all_ep_empty = str(payload.get("all_ep_empty", "0")).strip() in ("1", "true", "True", "ON", "on")

    # EP 라인 결정: 엔진이 보낸 ep_ports를 최우선으로 사용한다(가장 안정적).
    eps: List[str] = []
    ep_ports = payload.get("ep_ports", [])
    if isinstance(ep_ports, list) and ep_ports:
        eps = [str(x).strip().upper() for x in ep_ports if str(x).strip().upper().startswith("EP")]
    if not eps:
        # 폴백: occ 키
        eps = [str(k).strip().upper() for k in ep_occ.keys() if str(k).strip().upper().startswith("EP")]
    eps = sorted(eps, key=lambda x: int(str(x).upper().replace("EP", "") or "0"))
    if not eps:
        # 최후 폴백: 최소 2포트는 항상 보여준다(요구사항)
        eps = ["EP1", "EP2"]
    rows = list(eps) + ["ALL_EP"]

    rows_state = st.get("rows", {})
    if not isinstance(rows_state, dict):
        rows_state = {}
        st["rows"] = rows_state

    def _push(row: str, empty: bool, dur: float):
        segs = rows_state.get(row)
        if not isinstance(segs, list):
            segs = []
            rows_state[row] = segs
        if segs and isinstance(segs[-1], dict) and bool(segs[-1].get("empty")) == bool(empty):
            segs[-1]["dur"] = float(segs[-1].get("dur", 0.0)) + float(dur)
        else:
            segs.append({"empty": bool(empty), "dur": float(dur)})
        # 너무 길어지면 앞부분을 잘라 메모리/렌더 부담 완화(최근 200세그먼트 유지)
        if len(segs) > 220:
            del segs[:-200]

    # rows_state에 키를 미리 만들어, 렌더 시 줄이 항상 나오게 한다.
    for r in rows:
        if r not in rows_state or not isinstance(rows_state.get(r), list):
            rows_state[r] = []

    for ep in eps:
        v = str(ep_occ.get(ep, "EMPTY")).strip().upper()
        _push(ep, empty=(v == "EMPTY"), dur=dt)
    _push("ALL_EP", empty=bool(all_ep_empty), dur=dt)

    # 위젯 재구성(진행 로그 갱신 주기 수준이라 rebuild 비용 OK)
    old = ch.get("progress_ep_timeline_widget", None)
    if old is not None:
        try:
            old.destroy()
        except Exception:
            pass
        ch["progress_ep_timeline_widget"] = None

    # 요구사항: 막대 전체 길이를 "총 시뮬레이션 시간(예상)"으로 고정하고,
    # 그 안에서 비율만큼 채워지게 한다(슬라이딩 윈도우 금지).
    BAR_W = 320         # 진행현황 컬럼 기본 폭(창 너비에 맞게 짧게)
    BAR_H = 14
    NAME_W = 56

    t_end = float(sim_time)
    # total_est가 없거나 0이면(리셋/초기화 상태) "총시간 라벨"은 표시하지 않는다.
    # 단, 막대 스케일 계산을 위해 내부 total_est는 최소값(10s)을 사용한다.
    _total_raw = 0.0
    try:
        _total_raw = float(str(payload.get("sim_total_est_sec", "")).strip() or "0.0")
    except Exception:
        _total_raw = 0.0
    show_total_label = bool(isinstance(_total_raw, (float, int)) and float(_total_raw) > 0.0)
    total_est = max(10.0, float(_total_raw) if show_total_label else 0.0)
    if total_est <= 0.0:
        total_est = 10.0
    t_start = 0.0

    # 라벨이 너무 많으면(폭이 1px) 안 보이므로, 화면에 보일 만큼만 샘플링한다.
    # 목표: 최대 7~9개 정도만 표시.
    try:
        max_labels = 8
        raw_step = max(10.0, float(total_est) / float(max_labels))
        # 10초 단위로 올림
        tick_step = float(int(((raw_step + 9.999) // 10.0) * 10.0))
    except Exception:
        tick_step = 10.0
    tick_step = max(10.0, tick_step)

    def _color(empty: bool) -> int:
        # omni.ui 정수 색상 해석 이슈를 피하기 위해 명시값 사용
        return 0xFF0000FF if empty else 0xFF00FF00  # 빨강 / 초록

    with host:
        ch["progress_ep_timeline_widget"] = ui.VStack(spacing=4)

        # 상단 눈금(가독성 위해 최대 8개 내)
        with ui.HStack(height=12, spacing=0):
            ui.Spacer(width=NAME_W)
            with ui.ZStack(width=BAR_W, height=12):
                ui.Rectangle(width=BAR_W, height=12, style={"background_color": 0x441A1E26})
                # 막대 끝(우측)에 총 시뮬 시간을 표시(리셋/초기 상태에서는 숨김)
                if show_total_label:
                    try:
                        with ui.Placer(offset_x=max(0, BAR_W - 72), offset_y=0):
                            try:
                                _end_txt = (
                                    f"{int(round(total_est))}s"
                                    if abs(float(total_est) - float(int(round(total_est)))) < 1e-6
                                    else f"{float(total_est):.1f}s"
                                )
                            except Exception:
                                _end_txt = f"{total_est:.1f}s"
                            ui.Label(
                                _end_txt,
                                width=72,
                                height=12,
                                alignment=ui.Alignment.RIGHT_CENTER,
                                style={"color": 0xFFBFC7D5, "font_size": 10},
                            )
                    except Exception:
                        pass
                # 눈금 라벨을 절대좌표로 배치(겹침/폭 축소로 안 보이는 문제 방지)
                try:
                    ticks = int(total_est // tick_step)
                    ticks = max(1, ticks)
                except Exception:
                    ticks = 1
                for i in range(ticks + 1):
                    t_lbl = int(round(t_start + i * tick_step))
                    x = int(round((float(t_lbl) / float(total_est)) * float(BAR_W)))
                    x = max(0, min(BAR_W - 1, x))
                    with ui.Placer(offset_x=x, offset_y=0):
                        ui.Label(
                            f"{t_lbl}",
                            width=36,
                            height=12,
                            style={"color": 0xFFE0E6F0, "font_size": 10},
                        )
                # 마지막 총시간 라벨(정확값)을 반드시 끝에 추가(50단위만 보이는 문제 방지)
                try:
                    t_end_lbl = float(total_est)
                    try:
                        _end_tick_txt = (
                            f"{int(round(t_end_lbl))}"
                            if abs(float(t_end_lbl) - float(int(round(t_end_lbl)))) < 1e-6
                            else f"{float(t_end_lbl):.1f}"
                        )
                    except Exception:
                        _end_tick_txt = f"{t_end_lbl:.1f}"
                    with ui.Placer(offset_x=max(0, BAR_W - 36), offset_y=0):
                        ui.Label(
                            _end_tick_txt,
                            width=36,
                            height=12,
                            alignment=ui.Alignment.RIGHT_CENTER,
                            style={"color": 0xFFE0E6F0, "font_size": 10},
                        )
                except Exception:
                    pass

        for row in rows:
            with ui.HStack(height=BAR_H, spacing=6):
                ui.Label(str(row), width=NAME_W, height=BAR_H, style={"color": 0xFFBFC7D5})
                with ui.ZStack(width=BAR_W, height=BAR_H):
                    ui.Rectangle(width=BAR_W, height=BAR_H, style={"background_color": 0xFF1A1E26})
                    # 막대 세그먼트
                    segs = rows_state.get(row, [])
                    if not isinstance(segs, list):
                        segs = []
                    with ui.HStack(height=BAR_H, spacing=0):
                        used = 0
                        for s in (segs or []):
                            dur = float((s or {}).get("dur", 0.0))
                            if dur <= 1e-9:
                                continue
                            # total_est가 큰 경우 w가 0으로 반올림되어 막대가 안 보일 수 있어 최소 1px 보장
                            w = int(round((dur / total_est) * BAR_W))
                            w = max(1, w)
                            if w <= 0:
                                continue
                            used += w
                            ui.Rectangle(width=w, height=BAR_H, style={"background_color": _color(bool(s.get("empty", False)))})
                        # 남은 폭 채우기(빈 공간)
                        if used < BAR_W:
                            ui.Spacer(width=(BAR_W - used))


def _on_sim_event(ext: Any, payload: Dict[str, str]) -> None:
    seq_raw = (payload.get("seq") or "").strip()
    if not seq_raw:
        return
    seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)
    lot_id = payload.get("lot_id", "")
    sim_time = payload.get("sim_time", "")

    try:
        if seq in xml_generator.FROM_TO_SEQS:
            from_port = _parse_port_num(str(payload.get("from_port_id", "1")), 1)
            to_port = _parse_port_num(str(payload.get("to_port_id", "1")), 1)
            xml_text = xml_generator.build_xml_string(seq, from_port_id=from_port, to_port_id=to_port)
        else:
            port = _parse_port_num(str(payload.get("port_id", "1")), 1)
            xml_text = xml_generator.build_xml_string(seq, port_id=port)
        ext._last_generated_xml = xml_text
        parsed = xml_generator.parse_xml_string(xml_text) or {}
        story = f"[SIM EVENT t={sim_time}] seq={seq_raw}->{seq} lot={lot_id} port={payload.get('port_id','')} from={payload.get('from_port_id','')} to={payload.get('to_port_id','')}"
        if parsed.get("action_desc"):
            story += f" | action={parsed.get('action_desc')}"
        _append_sim_log(ext, story)
    except Exception as e:
        _append_sim_log(ext, f"[SIM EVENT] XML 생성/역파싱 실패: seq={seq}, err={e}")


def _parse_port_num(port_text: str, default_value: int = 1) -> int:
    """
    내부 포트 텍스트를 EAPEIS 포트 ID로 변환한다.
    매핑 규칙:
    - OHT -> 10 (MOVE FROM 가상 포트; EP/BP/INOUT과 충돌 없음)
    - EP1/2/3 -> 1/2/3
    - INOUT(=IN/OUT) -> 5
    - BP1/2/3/4 -> 6/7/8/9
    """
    txt = (port_text or "").strip().upper()
    if not txt:
        return default_value
    if txt in ("INOUT", "IN/OUT"):
        return 5
    if txt.startswith("OHT"):
        return 10
    if txt.startswith("EP"):
        try:
            n = int(txt.replace("EP", ""))
            if 1 <= n <= 3:
                return n
        except Exception:
            return default_value
    if txt.startswith("BP"):
        try:
            n = int(txt.replace("BP", ""))
            if 1 <= n <= 4:
                return 5 + n
        except Exception:
            return default_value
    if txt.startswith("PORT_"):
        txt = txt.replace("PORT_", "")
    try:
        return int(txt)
    except Exception:
        return default_value


def _port_kind(port_text: str) -> str:
    t = (port_text or "").strip().upper()
    if t.startswith("BP"):
        return "버퍼포트(BP)"
    if t.startswith("EP"):
        return "공정포트(EP)"
    if t.startswith("OHT"):
        return "이송장치(OHT)"
    return "미확인"


def _normalize_port_text_from_xml(parsed_val: str, original_text: str) -> str:
    """
    XML 역파싱 값(parsed_val)을 기준으로 포트 문자열을 표준화한다.
    - 원본이 BP/EP 접두사를 가지고 있으면 같은 접두사를 유지
    - 없으면 XML 숫자값 그대로 사용
    """
    p = (parsed_val or "").strip()
    if not p:
        return ""
    o = (original_text or "").strip().upper()
    try:
        n = int(p)
    except Exception:
        n = None
    if o.startswith("BP"):
        # XML ID 6~9는 BP1~4에 대응
        if n is not None and 6 <= n <= 9:
            return f"BP{n - 5}"
        return f"BP{p}"
    if o.startswith("EP"):
        # XML ID 1~3은 EP1~3에 대응
        if n is not None and 1 <= n <= 3:
            return f"EP{n}"
        return f"EP{p}"
    if o.startswith("INOUT") or o.startswith("IN/OUT"):
        return "INOUT"
    if o.startswith("OHT"):
        return "OHT"
    if n is not None:
        if 1 <= n <= 3:
            return f"EP{n}"
        if n == 5:
            return "INOUT"
        if 6 <= n <= 9:
            return f"BP{n - 5}"
    return p


def _foup_label_idle_text(screen_num: int, ep_id: str) -> str:
    """EP 별 FOUP 라벨의 'idle(대기)' 텍스트를 한 곳에서 생성한다(생성/리셋 일관성)."""
    if int(screen_num or 1) <= 1:
        return f"{ep_id} FOUP 공정: 대기"
    return f"{ep_id} FOUP 공정(화면{int(screen_num)}): 대기"


def _reset_foup_label_now(ext: Any, screen_idx: int, ep_id: str) -> None:
    """지정된 화면(screen_idx, 1-based) / EP 라벨 한 줄을 즉시 'idle(대기)' 표시로 되돌린다."""
    try:
        chans = getattr(ext, "_sim_monitor_channels", None) or []
        if not isinstance(chans, list) or not chans:
            return
        si = max(1, min(len(chans), int(screen_idx or 1)))
        chf = chans[si - 1] if isinstance(chans[si - 1], dict) else None
        if not chf:
            return
        labels = chf.get("foup_progress_labels") or {}
        lbl = labels.get(str(ep_id or "").strip().upper()) if isinstance(labels, dict) else None
        if lbl is None:
            return
        screen_num = int(chf.get("screen", si) or si)
        try:
            lbl.text = _foup_label_idle_text(screen_num, str(ep_id))
        except Exception:
            pass
        try:
            lbl.style = {"color": 0xFF888888}
        except Exception:
            pass
    except Exception:
        pass


def _reset_all_foup_labels(ext: Any) -> None:
    """모든 화면/EP 의 FOUP 라벨을 'idle(대기)' 표시로 일괄 리셋(Reset/Stop 안전망용)."""
    try:
        chans = getattr(ext, "_sim_monitor_channels", None) or []
        if not isinstance(chans, list):
            return
        for ch in chans:
            if not isinstance(ch, dict):
                continue
            try:
                screen_num = int(ch.get("screen", 1) or 1)
            except Exception:
                screen_num = 1
            labels = ch.get("foup_progress_labels") or {}
            if not isinstance(labels, dict):
                continue
            for ep_id, lbl in labels.items():
                if lbl is None:
                    continue
                try:
                    lbl.text = _foup_label_idle_text(screen_num, str(ep_id))
                except Exception:
                    pass
                try:
                    lbl.style = {"color": 0xFF888888}
                except Exception:
                    pass
    except Exception:
        pass


def _schedule_foup_label_reset(ext: Any, screen_idx: int, ep_id: str, delay_sec: float = 1.05) -> None:
    """
    FOUP_PROCESS 의 -Y(공정 종료 복귀) DONE 시점에서 ``delay_sec`` 후
    해당 화면/EP 라벨만 'idle(대기)' 로 되돌리는 1회성 update event subscription 을 등록한다.

    동작:
    - opts/A 의 ``_schedule_foup_inprogress_unmark`` 와 동일한 패턴(1회성 sub, deadline 기반).
    - subscription 객체는 GC 방지를 위해 ``ext._foup_label_reset_subs`` 에 보관.
    - Stop/Reset 시 해당 리스트도 일괄 정리(아래 on_sim_stop_clicked 안전망 참조).
    """
    try:
        import time as _t
        deadline = _t.monotonic() + max(0.0, float(delay_sec))
        sub_holder: Dict[str, Any] = {"sub": None, "done": False}

        def _on_update(_ev):
            try:
                if sub_holder.get("done"):
                    return
                if _t.monotonic() < deadline:
                    return
                sub_holder["done"] = True
                try:
                    _reset_foup_label_now(ext, int(screen_idx), str(ep_id))
                except Exception:
                    pass
                try:
                    s = sub_holder.get("sub")
                    if s is not None:
                        s.unsubscribe()
                except Exception:
                    pass
                sub_holder["sub"] = None
            except Exception:
                pass

        sub_holder["sub"] = app.get_app().get_update_event_stream().create_subscription_to_pop(
            _on_update, name="morph.tbs_control_2.foup_label_reset"
        )
        try:
            holders = getattr(ext, "_foup_label_reset_subs", None)
            if not isinstance(holders, list):
                holders = []
                ext._foup_label_reset_subs = holders
            holders.append(sub_holder)
            try:
                ext._foup_label_reset_subs = [h for h in holders if not h.get("done")]
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        try:
            _reset_foup_label_now(ext, int(screen_idx), str(ep_id))
        except Exception:
            pass


def _schedule_foup_inprogress_unmark(ext: Any, prim_path: str, delay_sec: float = 1.05) -> None:
    """
    FOUP_PROCESS_END 가 발생하면 -Y 복귀 애니가 끝나는 시점(약 1초 후)에
    port_lot_visibility 의 FOUP 진행중 표시를 해제한다.

    동작:
    - 1회성 update event subscription 으로 deadline 도달 시 unmark + self-unsubscribe.
    - subscription 객체가 GC 로 사라지지 않게 ext._foup_unmark_subs 에 보관한다.
    - 어떤 단계에서 실패하더라도, 마지막 안전망으로 즉시 unmark 호출(보호가 너무 길게 남는 것 방지).
    """
    try:
        from . import port_lot_visibility  # type: ignore
    except Exception:
        return
    p = str(prim_path or "").strip()
    if not p:
        return
    try:
        import time as _t
        deadline = _t.monotonic() + max(0.0, float(delay_sec))
        sub_holder: Dict[str, Any] = {"sub": None, "done": False}

        def _on_update(_ev):
            try:
                if sub_holder.get("done"):
                    return
                if _t.monotonic() < deadline:
                    return
                sub_holder["done"] = True
                try:
                    port_lot_visibility.mark_foup_in_progress(p, False)
                except Exception:
                    pass
                try:
                    s = sub_holder.get("sub")
                    if s is not None:
                        s.unsubscribe()
                except Exception:
                    pass
                sub_holder["sub"] = None
            except Exception:
                pass

        sub_holder["sub"] = app.get_app().get_update_event_stream().create_subscription_to_pop(
            _on_update, name="morph.tbs_control_2.foup_unmark"
        )
        try:
            holders = getattr(ext, "_foup_unmark_subs", None)
            if not isinstance(holders, list):
                holders = []
                ext._foup_unmark_subs = holders
            holders.append(sub_holder)
            # 다 끝난 항목 정리(메모리 누수 방지)
            try:
                ext._foup_unmark_subs = [h for h in holders if not h.get("done")]
            except Exception:
                pass
        except Exception:
            pass
    except Exception:
        try:
            port_lot_visibility.mark_foup_in_progress(p, False)
        except Exception:
            pass


def handle_sim_event_for_animation(ext: Any, payload: Dict[str, str], verbose: bool = True) -> None:
    """
    시뮬레이션 이벤트 → 애니메이션 실행 훅.

    이 함수가 하는 일(전체 흐름 한눈에):
    1) FOUP 공정 전용 이벤트(FOUP_PROCESS_START/END) → 별도 분기에서 prim Y축 ±이동만 실행 후 종료
    2) 그 외 일반 이벤트 → "이벤트 → XML 빌드 → 역파싱 → rules/map 매칭 → JSON 시퀀스 실행" 파이프라인
    3) 디버그 로그는 verbose 모드에서만 출력

    호출 경로(요약):
    - simulation_engine._emit_event(...) → ANIM_EVENT 큐 → _sim_ui_sink_anim_event(...) → 본 함수
    """

    # 0) 시퀀스 식별자(seq) 가져오기
    #    - 비어 있으면 처리할 게 없으므로 즉시 종료
    seq_raw = (payload.get("seq") or "").strip()
    if not seq_raw:
        return

    # ─────────────────────────────────────────────────────────────────────
    # 1) FOUP 공정 전용 분기 (FOUP_PROCESS_START / FOUP_PROCESS_END)
    #    - XML/매핑/JSON 시퀀스 파이프라인을 "타지 않는다".
    #    - port_lot_prim_paths.json 매핑으로 EP 포트의 FOUP prim 경로를 찾고,
    #      START 시 +Y320, END 시 -Y320 만큼 1초 동안 이동시킨다.
    #    - 분할화면(보조 USD 컨텍스트)도 고려한다(ctx_nm).
    # ─────────────────────────────────────────────────────────────────────
    try:
        seq_u0 = str(seq_raw or "").strip().upper()
    except Exception:
        seq_u0 = ""
    if seq_u0 in ("FOUP_PROCESS_START", "FOUP_PROCESS_END"):
        # 1-A) 어떤 EP 포트의 FOUP 인지 확인
        #     - port_id 가 없으면 매핑할 prim 을 못 찾으므로 종료
        try:
            port_id = str(payload.get("port_id", "") or "").strip().upper()
        except Exception:
            port_id = ""
        if not port_id:
            return
        # 1-B) port_lot_prim_paths.json → 해당 EP 포트의 FOUP prim 경로 조회
        try:
            from . import port_lot_visibility  # type: ignore
            mp = port_lot_visibility.load_port_lot_prim_paths() or {}
        except Exception:
            mp = {}
        prim_path = str((mp or {}).get(port_id, "") or "").strip()
        if not prim_path:
            if verbose:
                print(f"[FOUP] prim path missing: port={port_id}", flush=True)
            return
        # 1-C) 분할 화면별 USD 컨텍스트 결정(어떤 화면의 stage 위에서 움직일지)
        try:
            scr = int(str(payload.get("tbs_sim_screen", "1") or "1").strip() or "1")
        except Exception:
            scr = 1
        ctx_nm = _usd_context_name_for_sim_screen(ext, scr)
        # 1-D) FOUP 진행중 보호 마킹(옵션 A):
        #     - START: 즉시 mark(True) → 다른 시퀀스가 중간에 시작해도 baseline 복원에서 제외되어
        #              +Y320 오프셋이 유지된다.
        #     - END: -Y320 복귀 애니(약 1초)가 끝난 시점에 mark(False) 로 해제(지연 unmark).
        #            (END 직후 즉시 해제하면 -Y 복귀 도중 다른 시퀀스의 restore 가 prim 을
        #             baseline 으로 스냅시켜 시각적 점프가 발생할 수 있어 1초 지연한다.)
        try:
            from . import port_lot_visibility as _plv  # type: ignore
            if seq_u0 == "FOUP_PROCESS_START":
                _plv.mark_foup_in_progress(prim_path, True)
                # plateau 진입은 +Y 1초 애니가 끝난 시점에 표시(아래 1-E 에서 on_completed 콜백).
            else:  # FOUP_PROCESS_END
                # END 진입 즉시 plateau 해제 — 곧 시작될 -Y 애니가 baseline+320 강제 set 과 충돌하지 않게 한다.
                _plv.mark_foup_lifted(prim_path, False)
                _schedule_foup_inprogress_unmark(ext, prim_path, delay_sec=1.05)
        except Exception:
            pass
        # 1-D-2) Material 바인딩(요청 사양):
        #     - START: 공정 진행 중 material(MATERIAL_PATH_FOUP_PROCESSING, 기본 CASE_02)
        #     - END  : 공정 종료(회수 대기) material(MATERIAL_PATH_FOUP_DONE, 기본 CASE_03)
        #     - 회수(REMOVED) 후 “포트상태 초기화 시점”에 phong1 로 복귀하는 처리는
        #       port_lot_visibility.apply_port_lot_prim_visibility_for_context 의
        #       has_lot=False 분기에서 일괄 처리한다.
        #     - 분할화면(보조 USD 컨텍스트)을 고려해 ctx_nm 을 그대로 전달.
        try:
            from . import port_lot_visibility as _plv  # type: ignore
            mat_path = (
                _plv.MATERIAL_PATH_FOUP_PROCESSING
                if seq_u0 == "FOUP_PROCESS_START"
                else _plv.MATERIAL_PATH_FOUP_DONE
            )
            _plv.apply_port_lot_prim_material_for_context(ctx_nm, prim_path, mat_path)
        except Exception:
            pass
        # 1-E) START 면 +Y320, END 면 -Y320 (1.0초 부드러운 이동)
        #     - 좌표 단위(320)는 USD 스테이지 단위에 맞춰 사용자가 조정한 값
        #     - 같은 prim 의 진행 중 translate 가 있으면 먼저 정지(중첩 방지)
        dy = 320 if seq_u0 == "FOUP_PROCESS_START" else -320
        try:
            stop_prim_translate_animation(prim_path, usd_context_name=ctx_nm)
        except Exception:
            pass

        on_completed_cb = None
        if seq_u0 == "FOUP_PROCESS_START":
            def _on_lift_completed(_pp: str = prim_path) -> None:
                # +Y 1초 애니가 끝났다 = prim 이 baseline+320 자리에 도달했다.
                # 이후 "포트 초기화" 가 들어와도 baseline+320 으로 강제 set 되어 자리 유지.
                try:
                    from . import port_lot_visibility as _plv2  # type: ignore
                    _plv2.mark_foup_lifted(_pp, True)
                except Exception:
                    pass
            on_completed_cb = _on_lift_completed

        try:
            run_prim_translate_animation(
                prim_path,
                [{"duration": 1.0, "delta": (0.0, float(dy), 0.0)}],
                loop=False,
                on_completed=on_completed_cb,
                usd_context_name=ctx_nm,
            )
        except Exception:
            pass
        # 1-F) FOUP 분기는 여기서 종료(매핑/JSON 파이프라인을 타지 않는다)
        return

    # ─────────────────────────────────────────────────────────────────────
    # 2) 일반 이벤트(ARRIVED/MOVE_TRANSFERING/MOVE_REQ/REMOVED 등) 처리
    # ─────────────────────────────────────────────────────────────────────

    # 2-A) seq 별칭 정규화(내부 alias → 표준 시퀀스명)
    seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)
    # 2-B) payload 에서 자주 쓰는 필드 미리 추출(가독성/로그용)
    sim_time = payload.get("sim_time", "")
    lot_id = payload.get("lot_id", "")
    from_port_txt = str(payload.get("from_port_id", ""))
    to_port_txt = str(payload.get("to_port_id", ""))
    port_txt = str(payload.get("port_id", ""))
    from_kind = _port_kind(from_port_txt)
    to_kind = _port_kind(to_port_txt)
    port_kind = _port_kind(port_txt)

    # 2-C) 디버그 한 줄 로그(verbose 모드)
    if verbose:
        print(
            f"[ANIM HOOK t={sim_time}] 이벤트={seq_raw}->{seq} lot={lot_id} "
            f"port={port_txt}({port_kind}) from={from_port_txt}({from_kind}) to={to_port_txt}({to_kind})",
            flush=True,
        )

    # 2-D) 정책상 애니메이션이 없는 이벤트는 즉시 종료
    #     - READYTOLOAD/READYTOUNLOAD 는 "준비됨" 알림 성격이라 연계 애니가 없다.
    #     - rules/map 에 잘못 남아 있어도 여기서 차단(과실행 방지)
    try:
        if str(seq).strip().upper() in (
            str(xml_generator.SEQ_READYTOLOAD).strip().upper(),
            str(xml_generator.SEQ_READYTOUNLOAD).strip().upper(),
        ):
            if verbose:
                print(f"[ANIM HOOK] no-anim event skip: {seq}", flush=True)
            return
    except Exception:
        pass

    # 2-E) 주 실행 파이프라인:
    #      이벤트 → XML 생성 → 역파싱 → 표준화된 mapping_payload → rules/map 매칭 → JSON 시퀀스 실행
    #
    #      유지보수 규칙(중요):
    #      - rules/map 매칭 입력은 "반드시" XML 역파싱 결과를 기준으로 표준화한다.
    #      - 시뮬 payload 원본을 바로 매칭에 쓰지 않는다(포맷 드리프트 방지).
    parsed: Dict[str, Any] = {}
    xml_text = ""
    seq_for_mapping = seq
    try:
        # 2-E-1) 이벤트 종류에 따라 from/to 또는 port 만으로 XML 텍스트 생성
        if seq in xml_generator.FROM_TO_SEQS:
            from_port = _parse_port_num(from_port_txt, 1)
            to_port = _parse_port_num(to_port_txt, 1)
            xml_text = xml_generator.build_xml_string(seq, from_port_id=from_port, to_port_id=to_port)
        else:
            port = _parse_port_num(port_txt, 1)
            xml_text = xml_generator.build_xml_string(seq, port_id=port)
        # 2-E-2) XML 을 다시 dict 로 역파싱하여 표준 sequence_name 추출
        parsed = xml_generator.parse_xml_string(xml_text) or {}
        parsed_seq = str(parsed.get("sequence_name", "")).strip().upper()
        if parsed_seq:
            # 2-E-3) XML 이 알려주는 정식 sequence 를 매칭 키로 최우선 채택
            seq_for_mapping = parsed_seq
    except Exception as e:
        if verbose:
            print(f"[ANIM HOOK] XML 생성/역파싱 실패: seq={seq}, err={e}", flush=True)
        return

    # 2-F) rules/map 매칭 입력 표준화
    #     - 원본 payload 복사본에 XML 역파싱 결과로 from/to/port/seq 를 정규화해서 덮어쓴다.
    #     - 원본 텍스트의 접두사(BP/EP/OHT) 힌트는 _normalize_port_text_from_xml 가 보존한다.
    mapping_payload = dict(payload or {})
    parsed_from = str(parsed.get("from_port_id", "") or "")
    parsed_to = str(parsed.get("to_port_id", "") or "")
    parsed_port = str(parsed.get("port_id", "") or "")
    mapping_payload["seq"] = seq_for_mapping
    mapping_payload["from_port_id"] = _normalize_port_text_from_xml(parsed_from, from_port_txt)
    mapping_payload["to_port_id"] = _normalize_port_text_from_xml(parsed_to, to_port_txt)
    mapping_payload["port_id"] = _normalize_port_text_from_xml(parsed_port, port_txt)
    mapping_payload["_xml_sequence_name"] = seq_for_mapping
    mapping_payload["_xml_text"] = xml_text
    mapping_payload["_xml_parsed"] = parsed

    # 2-G) 매칭(이벤트 → JSON 경로)
    #     - rules.json 우선 → fallback map.json 순으로 _resolve_event_animation_entry 가 결정
    #     - 매칭 성공 시 _execute_mapped_sequence_stub 가 SequenceRunner.run(...) 까지 트리거한다.
    mapped_json, mapped_meta, matched_rule, matched_source = _resolve_event_animation_entry(seq_for_mapping, mapping_payload)
    if mapped_json:
        _append_anim_history_log(
            ext,
            f"[ANIM MAP] source={matched_source or '-'} rule={matched_rule or '-'} event={seq_for_mapping} file={Path(str(mapped_json)).name}",
        )
        _execute_mapped_sequence_stub(ext, seq_for_mapping, mapping_payload, mapped_json, mapped_meta, matched_rule, verbose)
    elif verbose:
        # 매칭 없음: 콘솔에 어느 파일을 확인해야 하는지 힌트 출력
        print(
            f"[ANIM MAP] 이벤트={seq_for_mapping} 매핑 없음 "
            f"(config/event_animation_rules.json / event_animation_map.json 확인)",
            flush=True,
        )
    # 2-H) 매칭이 없으면 이력 로그(애니 히스토리) 한 줄 추가(필요 예시 파일명 추정)
    if not mapped_json:
        hint_name = f"{seq_for_mapping.lower()}.json".replace("eapeis_port_", "")
        _append_anim_history_log(
            ext,
            f"[ANIM] 매핑없음 | event={seq_for_mapping} | 필요한 예시파일={hint_name}",
        )
    # 2-I) 이후 출력에서 쓰는 텍스트도 XML 표준화 결과로 갱신(일관성 유지)
    from_port_txt = str(mapping_payload.get("from_port_id", ""))
    to_port_txt = str(mapping_payload.get("to_port_id", ""))
    port_txt = str(mapping_payload.get("port_id", ""))
    from_kind = _port_kind(from_port_txt)
    to_kind = _port_kind(to_port_txt)
    port_kind = _port_kind(port_txt)

    # 2-J) action_desc 가 있으면 디버그용 액션 한 줄 출력
    action_desc = parsed.get("action_desc", "")
    if action_desc and verbose:
        if port_txt:
            action_desc += f" | 대상포트={port_txt}({port_kind})"
        if from_port_txt or to_port_txt:
            action_desc += f" | 이동경로={from_port_txt}({from_kind})->{to_port_txt}({to_kind})"
        print(f"[ANIM HOOK ACTION] {action_desc}", flush=True)

    # 2-K) 시퀀스 종류별 디버그 분기
    #     - 실제 애니 실행은 위 _execute_mapped_sequence_stub 에서 이미 끝났고,
    #       여기서는 “어떤 종류의 이벤트가 통과했는지” 한 줄 로그만 남긴다.
    if seq_for_mapping == xml_generator.SEQ_READYTOLOAD:
        if verbose:
            print(f"[ANIM PLAN] READY_TO_LOAD 대기 상태 애니메이션 | port={port_txt}({port_kind})", flush=True)
    elif seq_for_mapping == xml_generator.SEQ_ARRIVED:
        if verbose:
            print(f"[ANIM PLAN] ARRIVED 안착 애니메이션 | port={port_txt}({port_kind}) lot={lot_id}", flush=True)
    elif seq_for_mapping == xml_generator.SEQ_MOVE_TRANSFERING:
        if verbose:
            print(f"[ANIM PLAN] MOVE_TRANSFERING 이송 애니메이션 | from={from_port_txt}({from_kind}) to={to_port_txt}({to_kind}) lot={lot_id}", flush=True)
    elif seq_for_mapping == xml_generator.SEQ_MOVE:
        if verbose:
            print(f"[ANIM PLAN] MOVE 이동 애니메이션 | from={from_port_txt}({from_kind}) to={to_port_txt}({to_kind}) lot={lot_id}", flush=True)
    elif seq_for_mapping == xml_generator.SEQ_READYTOUNLOAD:
        if verbose:
            print(f"[ANIM PLAN] READY_TO_UNLOAD 회수 준비 애니메이션 | port={port_txt}({port_kind}) lot={lot_id}", flush=True)
    elif seq_for_mapping == xml_generator.SEQ_REMOVED:
        if verbose:
            print(f"[ANIM PLAN] REMOVED 회수 완료 애니메이션 | port={port_txt}({port_kind}) lot={lot_id}", flush=True)
    else:
        if verbose:
            print(f"[ANIM PLAN] 미분류 이벤트 | seq={seq_for_mapping} payload={payload}", flush=True)


def _is_progress_only_mode(ext: Any) -> bool:
    # 표시모드 제거: 진행현황 전용 모드는 더 이상 사용하지 않는다.
    return False


def _export_sim_logs_to_xlsx(ext: Any) -> None:
    """
    시뮬 종료 시 자동 호출되어 ``data/sim_logs/sim_logs_YYYYmmdd_HHMMSS.xlsx`` 를 생성한다.

    출력 정책(요구사항):
    - 엑셀에는 **프리런 JSON 타임테이블만** 남긴다(진행현황/이력로그/리포트 시트는 만들지 않는다).
    - 시트는 **단 한 장(`타임테이블`)** 으로 통합한다. 화면 구분은 각 JSON 라인의 ``"screen"`` 키로 한다.
    - 1열에 JSON 라인을 한 줄씩 기록한다(헤더 라인 ``[SIM] 타임테이블(프리런) — 화면N`` 은 제외).
    - 정렬 우선순위: ``t`` (시뮬 시간) 오름차순 → ``screen`` 오름차순 → kind(event 우선, step 다음).

    데이터 출처(우선순위):
    1) ``ext._sim_prerun_results_by_screen`` (프리런 결과) → ``_build_prerun_timetable_text`` 로 재빌드
       (가장 신뢰할 수 있는 원본; UI 라벨에 잘려 표시되었어도 전체 라인을 복원할 수 있음)
    2) 화면별 ``history_label.text`` 에 이미 들어 있는 타임테이블 블록 (Fallback)
    """
    try:
        from openpyxl import Workbook  # type: ignore
    except Exception as e:
        _append_sim_log(ext, f"[SIM EXPORT] openpyxl import 실패: {e}")
        return

    out_dir = _extension_root_dir() / "data" / "sim_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"sim_logs_{ts}.xlsx"

    # 1순위: 프리런 결과로 JSON 타임테이블을 다시 빌드(원본 신뢰)
    timetable_by: Dict[int, str] = {}
    try:
        results = getattr(ext, "_sim_prerun_results_by_screen", None)
        if isinstance(results, dict) and results:
            built = _build_prerun_timetable_text(results) or {}
            if isinstance(built, dict):
                timetable_by = {int(k): str(v) for k, v in built.items() if str(v or "").strip()}
    except Exception:
        timetable_by = {}

    # 2순위(Fallback): UI 의 history_label.text 에서 타임테이블 블록을 그대로 가져온다.
    if not timetable_by:
        try:
            chans_x = getattr(ext, "_sim_monitor_channels", None) or []
            if isinstance(chans_x, list):
                for ch in chans_x:
                    if not isinstance(ch, dict):
                        continue
                    try:
                        si = int(ch.get("screen", 0))
                    except Exception:
                        continue
                    if si <= 0:
                        continue
                    hl = ch.get("history_label")
                    if hl is None:
                        continue
                    txt = str(getattr(hl, "text", "") or "").strip()
                    if txt:
                        timetable_by[si] = txt
        except Exception:
            pass

    # 모든 화면의 JSON 라인을 하나로 모아 시간순 정렬
    # all_rows 의 항목: (t_float, screen_int, kind_str, raw_json_line_str)
    all_rows: List[Tuple[float, int, str, str]] = []
    for si, txt in (timetable_by.items() if isinstance(timetable_by, dict) else []):
        for line in (txt or "").splitlines():
            s = (line or "").strip()
            if not s:
                continue
            if s.startswith("[SIM] 타임테이블(프리런)"):
                continue
            t_val = 0.0
            scr_val = int(si)
            kind_val = ""
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    try:
                        t_val = float(obj.get("t", 0.0))
                    except Exception:
                        t_val = 0.0
                    try:
                        scr_val = int(obj.get("screen", si))
                    except Exception:
                        scr_val = int(si)
                    kind_val = str(obj.get("kind", "") or "")
            except Exception:
                # JSON 파싱 실패해도 원문은 보존(정렬 키만 기본값)
                pass
            all_rows.append((float(t_val), int(scr_val), str(kind_val), s))

    kind_prio = {"event": 0, "step": 1}
    try:
        all_rows.sort(
            key=lambda r: (
                float(r[0]),
                int(r[1]),
                int(kind_prio.get(str(r[2]), 9)),
            )
        )
    except Exception:
        pass

    wb = Workbook()
    ws = wb.active
    ws.title = "타임테이블"
    if not all_rows:
        ws.cell(row=1, column=1, value="(타임테이블 없음)")
    else:
        for i, (_t, _s, _k, raw) in enumerate(all_rows, start=1):
            ws.cell(row=i, column=1, value=raw)

    wb.save(str(out_path))
    _append_sim_log(ext, f"[SIM EXPORT] 저장 완료: {out_path}")


def _detach_sim_update(ext: Any) -> None:
    """
    시뮼 tick·UI 구독을 정리한다.

    - ``_sim_thread_stop`` 을 set 한 뒤, **멀티 시** ``_sim_tick_threads`` 의 worker 를 순서대로 join 하고,
      단일 tick 스레드 ``_sim_thread`` 가 남아 있으면 join 한다.
    - 시뮼 로그 큐 드레인용 ``_sim_log_ui_sub`` 구독을 해제한다.
    """
    sub = getattr(ext, "_sim_update_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
        ext._sim_update_sub = None

    stop_evt = getattr(ext, "_sim_thread_stop", None)
    th = getattr(ext, "_sim_thread", None)
    tick_threads = list(getattr(ext, "_sim_tick_threads", None) or [])
    if stop_evt is not None:
        try:
            stop_evt.set()
        except Exception:
            pass
    for tth in tick_threads:
        if tth is not None:
            try:
                tth.join(timeout=1.0)
            except Exception:
                pass
    try:
        ext._sim_tick_threads = []
    except Exception:
        pass
    if th is not None:
        try:
            th.join(timeout=1.0)
        except Exception:
            pass
    ext._sim_thread = None
    ext._sim_thread_stop = None
    ui_sub = getattr(ext, "_sim_log_ui_sub", None)
    if ui_sub is not None:
        try:
            ui_sub.unsubscribe()
        except Exception:
            pass
        ext._sim_log_ui_sub = None

    # 공정설정 시간 우선 모드: 애니 중단 요청 플래그 정리
    try:
        ie = getattr(ext, "_sim_interrupt_anim_event", None)
        if ie is not None:
            try:
                ie.clear()
            except Exception:
                pass
    except Exception:
        pass
    try:
        ext._sim_interrupt_anim_event = None
        ext._sim_interrupt_anim_apply_fn = None
    except Exception:
        pass


def on_sim_start_clicked(ext: Any) -> None:
    """
    시뮬레이션 시작: 엔진(들) 생성, 콜백 연결, tick 스레드(들) 기동.

    - ``ext._sim_viewport_split_count`` 로 채널 수 N 을 정한다. N<=1 이면 단일 ``TBSSimulationEngine`` +
      기존 ``_tick_loop`` 스레드 한 개. N>1 이면 화면별 스냅샷으로 N 개 엔진을 만들고
      ``_sim_multi_engine_tick_worker`` 를 **화면마다 한 스레드씩** 띄운다(게이트·pause 독립).
    - 시작 전 ``on_sim_stop_clicked`` 로 이전 스레드·엔진을 정리한다.

    데이터/상태의 큰 흐름(추적용):
    - (UI) 이 함수에서 run 세대 토큰 ``ext._sim_run_gen`` 을 증가시킨다.
      - 목적: stop/reset 이후 큐에 남은 UI 업데이트/애니 이벤트가 "이전 실행" 것이라면 무시하도록 하기 위함.
    - (UI→엔진) `simulation_engine.py`의 ``TBSSimulationEngine``(또는 멀티 엔진 리스트)을 생성하고,
      엔진이 emit 하는 on_log/on_progress/on_anim_event 콜백을 `control_window.py`의 UI sink로 연결한다.
    - (엔진→UI) 엔진이 emit 한 progress payload에는 `ep_occ`, `all_ep_empty`, `sim_total_est_sec` 등이 포함되고,
      이는 아래 EP 타임라인 그래프/우측 누적 시간 표시로 연결된다.
      - 종료 요약(`[SUMMARY] EPn_EMPTY / ALL_EP_EMPTY`)은 엔진(`simulation_engine.py`)에서 계산/출력한다.
      - UI 막대 우측의 초 표시는 UI가 가진 그래프 세그먼트(rows_state) 합산값이다(개념/정의는 동일).

    스레드/구독(중요):
    - tick 스레드: 시뮬 시간을 실제로 전진시키는 워커(단일 1개 또는 화면별 N개).
    - EP 타임라인(포트상태 아래)은 **엔진의 SimPy ``env.now``를 ``timeline_only`` progress의 ``sim_time``으로** 갱신한다.
      (내부 ``virtual_now`` 예산 보간은 막대 축에 쓰지 않아, 진행현황 t(sim)과 동일한 시계를 유지한다.)
    """
    try:
        n_ch = max(1, min(4, int(getattr(ext, "_sim_viewport_split_count", 1) or 1)))
    except Exception:
        n_ch = 1

    on_sim_stop_clicked(ext)
    try:
        _restore_sim_prim_motion_to_initial(ext)
    except Exception:
        pass
    # FOUP 진행중 보호 표시 추가 안전망(이전 세션 잔여 차단):
    # - on_sim_stop_clicked 에서 이미 정리하지만, 어떤 경로로든 잔여가 남는 것을 막기 위해 한 번 더 비운다.
    try:
        from . import port_lot_visibility as _plv  # type: ignore
        _plv.clear_foup_in_progress()
    except Exception:
        pass
    # 포트 LOT prim 의 baseline transform 을 "시뮬 시작 직전(원위치)" 에 미리 캡처한다.
    # 이유:
    #   - baseline 캐시는 평소 SequenceRunner.run 진입 시 처음 잡힌다.
    #   - 그런데 첫 시퀀스가 FOUP_PROCESS_START 같은 비-SequenceRunner 이벤트면 baseline 이
    #     캡처되지 않은 채 +Y 320 애니가 먼저 prim 을 옮긴다. 이후 다른 시퀀스가 처음 SequenceRunner
    #     를 돌릴 때 이미 +320 위치가 baseline 으로 잘못 캡처되어, 후속 보정에서 +320+320=640 으로
    #     점프하는 누적 오프셋 버그가 발생한다.
    #   - 시작 시점에 prim 이 진짜 author 위치인 상태에서 캐시를 채워두면 이 경합이 원천 차단된다.
    try:
        from . import port_lot_visibility as _plv  # type: ignore
        _plv.clear_port_lot_authoring_cache()
        _plv.ensure_port_lot_authoring_captured()
    except Exception:
        pass
    # 시작을 반복할 때 모니터 UI(포트/그래프/진행/이력) 영역에 위젯이 누적되어
    # 버튼 아래의 빈 공간이 점점 커지는 현상이 발생할 수 있어,
    # 시작 시점에 모니터 영역을 한 번 깨끗하게 재빌드한다.
    try:
        _rebuild_sim_monitor_split_ui(ext)
    except Exception:
        pass
    # 실행 세대 토큰: stop/reset 후 남은 이벤트/애니 job을 무시하기 위해 사용
    try:
        ext._sim_run_gen = int(getattr(ext, "_sim_run_gen", 0) or 0) + 1
    except Exception:
        ext._sim_run_gen = 1
    _auto_fill_per_screen_snapshots_on_start(ext)
    if n_ch > 1:
        try:
            _ensure_tick_pause_map_for_multi(ext, n_ch)
        except Exception:
            pass

    # 공정 시간/간격/초기포트/고장포트 등 “시뮬 입력값”은 스냅샷(dict) 하나로 통일한다.
    # - 분할(N>1): 화면별로 저장된 스냅샷을 사용
    # - 단일(N==1): 화면1 스냅샷(없으면 현재 UI값을 캡처한 dict)을 사용
    ep_count = 2
    timing = SimulationTimingConfig()
    init_cfg = SimulationInitConfig(ep_count=2, initial_full_ports=[], max_oht_lots=6, process_time_priority=False)
    snap_1: Dict[str, Any] = {}
    if n_ch <= 1:
        try:
            snaps1 = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
        except Exception:
            snaps1 = [None, None, None, None]
        try:
            cap1 = _capture_per_screen_sim_settings(ext)
        except Exception:
            cap1 = {}
        s0 = snaps1[0] if len(snaps1) >= 1 else None
        snap_1 = dict(s0) if isinstance(s0, dict) else dict(cap1)
        try:
            timing, init_cfg = _timing_and_init_from_snapshot(ext, snap_1)
        except Exception:
            pass
        try:
            ep_count = int(getattr(init_cfg, "ep_count", 2) or 2)
        except Exception:
            ep_count = 2

    log_interval = max(0.0, ext._sim_log_interval_model.get_value_as_float())
    log_cfg = SimulationLogConfig(
        progress_interval_sec=log_interval,
        input_status_interval_sec=log_interval,
    )
    lots: List[Lot] = []

    ext._sim_history_text.set_value("[SIM] 초기화")
    ext._sim_progress_text.set_value("[진행현황] 초기화 (시뮬레이션 시작 대기)")
    ext._sim_port_state_text.set_value("[포트상태] 초기화 (이벤트 대기)")
    # EP 타임라인: 시작 버튼 누르는 순간부터(t=0) 빈 포트 상태로 표시/진행할 수 있도록 초기 스냅샷을 만든다.
    try:
        ext._sim_ep_occ_timeline_state_by_screen = {}
    except Exception:
        pass
    try:
        ext._sim_last_ports_occupancy_by_screen = {}
    except Exception:
        pass
    # 종료 시점(env.now)과의 정합을 위해 유지(엔진 timeline_only 경로와는 별개)
    try:
        ext._sim_ep_timeline_virtual_time_by_screen = {}
    except Exception:
        pass
    try:
        sub = getattr(ext, "_sim_ep_timeline_ui_sub", None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        ext._sim_ep_timeline_ui_sub = None
    except Exception:
        pass
    try:
        ext._sim_aux_anim_notice_screens = set()
    except Exception:
        pass
    chans0 = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans0, list) and len(chans0) > 1:
        for ch in chans0:
            if not isinstance(ch, dict):
                continue
            try:
                si = int(ch.get("screen", 1))
            except Exception:
                si = 1
            ht = "[SIM] 초기화" if si == 1 else f"[SIM·화면{si}] 초기화"
            pt = "[진행현황] 초기화 (시뮬레이션 시작 대기)" if si == 1 else f"[진행현황·화면{si}] 초기화 (대기)"
            ph = f"[포트상태·화면{si}] 초기화 (이벤트 대기)"
            hl = ch.get("history_label")
            pl = ch.get("progress_label")
            phdr = ch.get("port_header")
            if hl is not None:
                hl.text = ht
            if pl is not None:
                pl.text = pt
            if phdr is not None:
                phdr.text = ph
            cells = ch.get("port_cells") or {}
            boxes = ch.get("port_cell_boxes") or {}
            for port in ("INOUT", "BP2", "BP3", "BP4", "BP1", "EP1", "EP2"):
                if port in cells:
                    cells[port].text = "IN/OUT:-" if port == "INOUT" else f"{port}:-"
                try:
                    _set_port_box_style(ext, port, "-", boxes)
                except Exception:
                    pass
            ep3c = ch.get("port_ep3_cell")
            if ep3c is not None:
                ep3c.text = "EP3:-"
            try:
                _set_port_box_style(ext, "EP3", "-", boxes)
            except Exception:
                pass
            # EP 타임라인 초기 렌더(t=0.0) + 마지막 점유 스냅샷 저장
            try:
                occ0 = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
                ext._sim_last_ports_occupancy_by_screen[str(si)] = dict(occ0)  # type: ignore[index]
                _update_ep_timeline_under_port_state(ext, ch, occ0, "0.0")
            except Exception:
                pass
    else:
        if getattr(ext, "_sim_history_label", None) is not None:
            ext._sim_history_label.text = "[SIM] 초기화"
        if getattr(ext, "_sim_progress_label", None) is not None:
            ext._sim_progress_label.text = "[진행현황] 초기화 (시뮬레이션 시작 대기)"
        if getattr(ext, "_sim_port_state_label", None) is not None:
            ext._sim_port_state_label.text = "[포트상태] 초기화 (이벤트 대기)"
        if getattr(ext, "_sim_port_state_header_label", None) is not None:
            ext._sim_port_state_header_label.text = "[포트상태] 초기화 (이벤트 대기)"
        cells = getattr(ext, "_sim_port_cells", {}) or {}
        for port in ("INOUT", "BP2", "BP3", "BP4", "BP1", "EP1", "EP2"):
            if port in cells:
                cells[port].text = "IN/OUT:-" if port == "INOUT" else f"{port}:-"
        if getattr(ext, "_sim_port_ep3_cell", None) is not None:
            ext._sim_port_ep3_cell.text = "EP3:-"
        # 단일(또는 모니터 채널 1개) 모드: 시작 직후 timeline_only 가 last_occ 없이 스킵되지 않도록 시드
        try:
            occ0 = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
            by = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
            if not isinstance(by, dict):
                by = {}
                ext._sim_last_ports_occupancy_by_screen = by
            by["1"] = dict(occ0)
            chans_s = getattr(ext, "_sim_monitor_channels", None)
            if isinstance(chans_s, list) and len(chans_s) >= 1 and isinstance(chans_s[0], dict):
                _update_ep_timeline_under_port_state(ext, chans_s[0], occ0, "0.0")
        except Exception:
            pass

    # -------------------------------------------------------------------
    # Start 직후(t=0) 초기 적재 포트(FULL) 즉시 반영
    # - 기존에는 occ0(전부 EMPTY)로 먼저 그린 뒤, 첫 공정/첫 이벤트까지 업데이트가 없어
    #   포트상태/막대가 "첫 공정 전까지 빨강"처럼 보일 수 있었다.
    # -------------------------------------------------------------------
    def _occ_from_snap(snap: Dict[str, Any], ep_cnt: int) -> Dict[str, str]:
        occ = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
        try:
            if bool(snap.get("init_inout")):
                occ["INOUT"] = "FULL"
            if bool(snap.get("init_bp1")):
                occ["BP1"] = "FULL"
            if bool(snap.get("init_bp2")):
                occ["BP2"] = "FULL"
            if bool(snap.get("init_bp3")):
                occ["BP3"] = "FULL"
            if bool(snap.get("init_bp4")):
                occ["BP4"] = "FULL"
            if bool(snap.get("init_ep1")):
                occ["EP1"] = "FULL"
            if bool(snap.get("init_ep2")):
                occ["EP2"] = "FULL"
            if bool(snap.get("init_ep3")):
                occ["EP3"] = "FULL"
        except Exception:
            pass
        if int(ep_cnt) < 3:
            occ["BP4"] = ""
            occ["EP3"] = ""
        return occ

    try:
        snaps_init = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
    except Exception:
        snaps_init = [None, None, None, None]
    while len(snaps_init) < 4:
        snaps_init.append(None)
    snaps_init = snaps_init[:4]

    for scr0 in range(1, int(n_ch) + 1):
        s0 = snaps_init[scr0 - 1] if (scr0 - 1) < len(snaps_init) else None
        if not isinstance(s0, dict):
            try:
                s0 = _capture_per_screen_sim_settings(ext)
            except Exception:
                s0 = {}
        try:
            ep_idx0 = int(s0.get("ep_count_idx", 0) or 0)
        except Exception:
            ep_idx0 = 0
        ep_cnt0 = 2 if int(ep_idx0) == 0 else 3
        occ_init = _occ_from_snap(s0, ep_cnt0)
        try:
            ext._sim_last_ports_occupancy_by_screen[str(scr0)] = dict(occ_init)  # type: ignore[index]
        except Exception:
            pass
        try:
            _update_port_occupancy_panel(ext, occ_init, sim_time="0.0", screen=int(scr0))
        except Exception:
            pass
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}
    ext._sim_log_queue = queue.SimpleQueue()
    _enqueue_sim_log(ext, "[SIM UI] 실시간 로그 큐 초기화")
    # 첫 공정 전에도 진행현황이 끊기지 않도록, 화면별 기본 progress payload를 1회 시드한다.
    try:
        for scr0 in range(1, int(n_ch) + 1):
            p0 = {
                "tbs_sim_screen": str(scr0),
                "sim_time": "0.00",
                "label": "대기",
                "detail": "",
                "status": "RUNNING",
                "elapsed": "0.0",
                "total": "0.0",
                "percent": "0",
            }
            _enqueue_sim_progress(ext, p0)
    except Exception:
        pass
    ext._sim_anim_active = {}
    ext._sim_anim_pending = []

    def _interrupt_anim_for_proc_priority(screen: Optional[str] = None) -> None:
        """
        공정설정 시간 우선 모드에서 '애니가 재생 중이어도 끊고 다음 단계로 진행'을 위해 호출.
        - 시뮬 tick pause 플래그를 해제하고
        - 시퀀스 러너/개별 애니메이션을 stop 한다.
        """
        try:
            scr = str(screen or "").strip() or None
            # pending 큐 비우기(현재 모드에서는 애니 길이를 기다리지 않음)
            if scr is not None:
                pending_by = getattr(ext, "_sim_anim_pending_by_screen", None)
                if isinstance(pending_by, dict):
                    pending_by[str(scr)] = []
            else:
                ext._sim_anim_pending = []
        except Exception:
            pass
        try:
            # 멀티 화면: 화면별 runner가 있으면 전부 정리.
            runners = getattr(ext, "_sim_runners_by_screen", None)
            if isinstance(runners, dict) and runners:
                if scr is not None:
                    r = runners.get(str(scr))
                    try:
                        if r is not None:
                            r.pause()
                    except Exception:
                        pass
                else:
                    for r in list(runners.values()):
                        try:
                            if r is not None:
                                r.pause()
                        except Exception:
                            pass
            else:
                runner = getattr(ext, "_sim_runner", None)
                if runner is not None:
                    runner.pause()
        except Exception:
            pass
        try:
            stop_all_translate_animations(preserve_foup_port_lot_prims=True)
        except Exception:
            pass
        try:
            stop_all_rotate_animations()
        except Exception:
            pass
        try:
            stop_all_curve_animations()
        except Exception:
            pass
        pe = getattr(ext, "_sim_tick_pause_event", None)
        if pe is not None:
            try:
                pe.clear()
            except Exception:
                pass
        try:
            ext._sim_tick_pause_until_wall = None
        except Exception:
            pass

    # tick 스레드 -> UI 스레드 마샬링: 중단 요청 플래그만 세팅하고, 실제 stop은 _drain_sim_log_queue에서 처리.
    try:
        ext._sim_interrupt_anim_event = threading.Event()
        ext._sim_interrupt_anim_apply_fn = lambda: _interrupt_anim_for_proc_priority(None)
        # 화면별 interrupt (멀티 시뮬에서 해당 화면만 중단)
        ext._sim_interrupt_anim_event_by_screen = {}
        ext._sim_interrupt_anim_apply_fn_by_screen = {}
        try:
            n_ch2 = int(getattr(ext, "_sim_split_channels", 1) or 1)
        except Exception:
            n_ch2 = 1
        n_ch2 = max(1, n_ch2)
        for i in range(n_ch2):
            scr = str(i + 1)
            ext._sim_interrupt_anim_event_by_screen[scr] = threading.Event()
            ext._sim_interrupt_anim_apply_fn_by_screen[scr] = (lambda s=scr: _interrupt_anim_for_proc_priority(s))
    except Exception:
        ext._sim_interrupt_anim_event = None
        ext._sim_interrupt_anim_apply_fn = None
        ext._sim_interrupt_anim_event_by_screen = None
        ext._sim_interrupt_anim_apply_fn_by_screen = None

    def _request_interrupt_anim_for_proc_priority(tags: Optional[Dict[str, Any]] = None) -> None:
        # 화면별 중단 지원: tbs_sim_screen이 있으면 해당 화면만 중단 요청.
        try:
            scr = "1"
            if isinstance(tags, dict):
                scr = str(tags.get("tbs_sim_screen", "1") or "1").strip() or "1"
            # per-screen event가 있으면 그쪽을 우선
            by = getattr(ext, "_sim_interrupt_anim_event_by_screen", None)
            if isinstance(by, dict) and scr in by and by[scr] is not None:
                by[scr].set()
                return
        except Exception:
            pass
        try:
            ie = getattr(ext, "_sim_interrupt_anim_event", None)
            if ie is not None:
                ie.set()
        except Exception:
            pass

    def _on_gate(payload: Dict[str, str]) -> float:
        # 요구사항: 공정시간보다 애니(JSON) 시간이 길면 다음 공정은 애니 종료까지 대기.
        # simulation_engine은 이 반환값(초)을 받아서 각 공정 timeout을 max(공정, 애니)로 확장한다.
        anim_est_sec = _estimate_anim_duration_for_gate_payload(ext, payload or {})
        # 공정확인 체크 시에는 "확인 클릭 전에는 애니/공정 시작 금지"가 목표이므로,
        # simulation_engine의 _request_gate() 시점에 UI 확인창을 띄우고 동기 블로킹한다.
        try:
            confirm_each = bool(
                getattr(ext, "_sim_confirm_each_step_model", None) is not None
                and ext._sim_confirm_each_step_model.get_value_as_bool()
            )
        except Exception:
            confirm_each = False
        if not confirm_each:
            return float(anim_est_sec)

        # 공정확인 중에는 sim tick thread도 멈춰야 "확인 전까지 완전 정지"가 된다.
        try:
            gp = getattr(ext, "_sim_gate_pause_event", None)
            if gp is not None:
                gp.set()
        except Exception:
            pass

        seq_raw = str(payload.get("seq", ""))
        seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)
        lot = str(payload.get("lot_id", ""))
        est = str(payload.get("est_sec", ""))
        fr = str(payload.get("from_port_id", ""))
        to = str(payload.get("to_port_id", ""))
        port = str(payload.get("port_id", ""))
        try:
            # 게이트 다이얼로그의 XML도 실제 애니 매핑 파이프라인과 같은 규칙으로 생성한다.
            # (FROM/TO 시퀀스 vs PORT_ID 시퀀스 분기)
            if seq in xml_generator.FROM_TO_SEQS:
                fnum = _parse_port_num(fr, 1)
                tnum = _parse_port_num(to, 1)
                xml = xml_generator.build_xml_string(seq, from_port_id=fnum, to_port_id=tnum)
            elif seq in xml_generator.PORT_ID_ONLY_SEQS:
                pnum = _parse_port_num(port, 1)
                xml = xml_generator.build_xml_string(seq, port_id=pnum)
            else:
                xml = f"(XML 미생성: 비-XML 공정 seq={seq_raw}->{seq})"
        except Exception:
            xml = f"(XML 생성 실패: seq={seq})"

        xml_sequence_name = ""
        if isinstance(xml, str) and xml.strip().startswith("<"):
            try:
                _pd = xml_generator.parse_xml_string(xml) or {}
                xml_sequence_name = str(_pd.get("sequence_name", "") or "").strip().upper()
            except Exception:
                pass

        # Alert에서 "실행 대상 JSON 파일"과 존재 여부를 함께 안내한다.
        map_line = "JSON 매핑: 없음"
        try:
            mapping_payload = dict(payload or {})
            seq_for_mapping = seq
            if isinstance(xml, str) and xml.strip().startswith("<"):
                parsed = xml_generator.parse_xml_string(xml) or {}
                parsed_seq = str(parsed.get("sequence_name", "") or "").strip().upper()
                if parsed_seq:
                    seq_for_mapping = parsed_seq
                mapping_payload["seq"] = seq_for_mapping
                mapping_payload["from_port_id"] = _normalize_port_text_from_xml(str(parsed.get("from_port_id", "") or ""), fr)
                mapping_payload["to_port_id"] = _normalize_port_text_from_xml(str(parsed.get("to_port_id", "") or ""), to)
                mapping_payload["port_id"] = _normalize_port_text_from_xml(str(parsed.get("port_id", "") or ""), port)
            else:
                mapping_payload["seq"] = seq_for_mapping

            mapped_json, _meta, rule_name, source_name = _resolve_event_animation_entry(seq_for_mapping, mapping_payload)
            if mapped_json:
                jp = _normalize_json_path(mapped_json)
                exists_txt = "존재" if jp.is_file() else "없음"
                map_line = (
                    f"JSON 매핑: source={source_name or '-'} rule={rule_name or '-'} "
                    f"file={jp.name} ({exists_txt})"
                )
            else:
                map_line = f"JSON 매핑: 없음 (event={seq_for_mapping})"
        except Exception as e:
            map_line = f"JSON 매핑 확인 실패: {e}"
        done_evt = threading.Event()
        message = (
            f"공정: {payload.get('title','-')}\n"
            f"이벤트 sequence_name: 시뮬 seq={seq_raw or '-'}, 규격/별칭={seq or '-'}"
            + (f", XML SEQUENCE_NAME={xml_sequence_name}" if xml_sequence_name else "")
            + "\n"
            f"lot={lot} from={fr} to={to} port={port}\n"
            f"예상시간={est}s\n"
            f"애니예상={anim_est_sec:.2f}s (JSON 기준)\n\n"
            f"{map_line}\n\n"
            f"XML:\n{xml}"
        )
        _enqueue_gate_request(
            ext,
            {
                "title": payload.get("title", "공정 확인"),
                "message": message,
                "_done_event": done_evt,
                "gate_seq_raw": seq_raw,
                "gate_seq_canonical": seq,
                "gate_xml_sequence_name": xml_sequence_name,
            },
        )
        # 시뮬레이션 스레드는 사용자 확인 전까지 여기서 동기 대기한다.
        done_evt.wait()
        return float(anim_est_sec)

    try:
        ext._sim_engines = []
    except Exception:
        pass

    if n_ch <= 1:

        def _collect_faulty_ports_for_engine() -> Set[str]:
            # 단일도 스냅샷(dict) 기준으로 통일(= UI 모델/다른 경로 중복 제거)
            try:
                return set(_fault_ports_from_snapshot(snap_1, ep_count))
            except Exception:
                return set()

        # 프리런/재생 모드에서는 엔진 콜백을 UI로 직접 보내지 않는다(프리런 수집 → 재생 단계에서만 UI로 emit).
        engine = TBSSimulationEngine(
            lots=lots,
            timing=timing,
            log_config=log_cfg,
            init_config=init_cfg,
            on_log=lambda _line: None,
            on_event=lambda _payload: None,
            on_progress=lambda _payload: None,
            # 프리런은 사용자 확인 UI 없이 자동 통과 + 애니 길이만 반영
            on_gate=lambda payload: float(_estimate_anim_duration_for_gate_payload(ext, payload or {})),
            print_to_console=(not _is_progress_only_mode(ext)),
            event_tags={"tbs_sim_screen": "1"},
        )
        try:
            engine.set_runtime_hooks(
                faulty_ports_supplier=_collect_faulty_ports_for_engine,
                interrupt_anim_cb=_request_interrupt_anim_for_proc_priority,
            )
        except Exception:
            pass
        ext._sim_engine = engine
        ext._sim_engines = []
        # start() 직전에 스케일 주입: 시작 직후 이벤트가 큐에 들어가도 30초 폴백에 고정되지 않게 한다.
        try:
            te0 = float(getattr(engine, "_sim_total_est_sec", 0.0) or 0.0)
        except Exception:
            te0 = 0.0
        if te0 > 0.0:
            try:
                by = getattr(ext, "_sim_last_total_est_by_screen", None)
                if not isinstance(by, dict):
                    by = {}
                    ext._sim_last_total_est_by_screen = by
                by["1"] = float(te0)
            except Exception:
                pass
        if not engine.start():
            _append_sim_log(ext, "[SIM] 시작 실패")
            return
    else:
        try:
            snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
        except Exception:
            snaps = [None, None, None, None]
        while len(snaps) < 4:
            snaps.append(None)
        snaps = snaps[:4]
        cap = _capture_per_screen_sim_settings(ext)
        engines: List[Any] = []

        def _make_fault_supplier(sf: Dict[str, Any], ec: int):
            def _sup() -> Set[str]:
                return set(_fault_ports_from_snapshot(sf, ec))

            return _sup

        # 요구사항: 화면2~4는 "저장" 전까지 현재 UI 변경이 즉시 반영되면 안 된다.
        # 따라서 snap_i가 None이면,
        # - 화면1은 현재 UI 캡처(cap)로 폴백(기본값)
        # - 화면2~4는 화면1 스냅샷(기본값)이 있으면 그걸 폴백, 없으면 cap 폴백
        base_snap = None
        try:
            base_snap = snaps[0] if (len(snaps) >= 1 and isinstance(snaps[0], dict)) else None
        except Exception:
            base_snap = None
        for i in range(n_ch):
            snap_i = snaps[i]
            if snap_i is None:
                if i == 0:
                    snap_i = copy.deepcopy(cap)
                elif isinstance(base_snap, dict):
                    snap_i = copy.deepcopy(base_snap)
                else:
                    snap_i = copy.deepcopy(cap)
            timing_i, init_i = _timing_and_init_from_snapshot(ext, snap_i)
            screen_tag = str(i + 1)
            snap_frozen = copy.deepcopy(snap_i)
            ep_i = int(getattr(init_i, "ep_count", 2) or 2)
            ep_i = 3 if ep_i >= 3 else 2
            # 프리런/재생 모드: 콜백은 프리런 수집 단계에서만 사용(여기선 노옵 주입).
            eng = TBSSimulationEngine(
                lots=lots,
                timing=timing_i,
                log_config=log_cfg,
                init_config=init_i,
                on_log=lambda _line: None,
                on_event=lambda _payload: None,
                on_progress=lambda _payload: None,
                on_gate=lambda payload: float(_estimate_anim_duration_for_gate_payload(ext, payload or {})),
                print_to_console=(not _is_progress_only_mode(ext)),
                event_tags={"tbs_sim_screen": screen_tag},
            )
            try:
                eng.set_runtime_hooks(
                    faulty_ports_supplier=_make_fault_supplier(snap_frozen, ep_i),
                    interrupt_anim_cb=_request_interrupt_anim_for_proc_priority,
                )
            except Exception:
                pass
            engines.append(eng)

        # 멀티: start() 전에 화면별 총 예상 시간을 넣어 두어 첫 타임라인 갱신이 30초 스케일에 묶이지 않게 한다.
        try:
            by_pre = getattr(ext, "_sim_last_total_est_by_screen", None)
            if not isinstance(by_pre, dict):
                by_pre = {}
                ext._sim_last_total_est_by_screen = by_pre
            for i_pre, eng_pre in enumerate(engines):
                if eng_pre is None:
                    continue
                try:
                    te_pre = float(getattr(eng_pre, "_sim_total_est_sec", 0.0) or 0.0)
                except Exception:
                    te_pre = 0.0
                if te_pre > 0.0:
                    by_pre[str(i_pre + 1)] = float(te_pre)
        except Exception:
            pass

        started: List[Any] = []
        for eng in engines:
            if not eng.start():
                for e2 in started:
                    try:
                        e2.stop()
                    except Exception:
                        pass
                try:
                    ext._sim_engine = None
                    ext._sim_engines = []
                except Exception:
                    pass
                _append_sim_log(ext, "[SIM] 멀티 채널 시작 실패")
                return
            started.append(eng)
        # 화면별 EP 타임라인 스케일(총 예상 시간) 선주입
        try:
            by = getattr(ext, "_sim_last_total_est_by_screen", None)
            if not isinstance(by, dict):
                by = {}
                ext._sim_last_total_est_by_screen = by
            for i, eng in enumerate(started):
                scr = str(i + 1)
                try:
                    te = float(getattr(eng, "_sim_total_est_sec", 0.0) or 0.0)
                except Exception:
                    te = 0.0
                if te > 0.0:
                    by[scr] = float(te)
        except Exception:
            pass
        try:
            ext._sim_engines = engines
            ext._sim_engine = engines[0] if engines else None
        except Exception:
            pass
        try:
            _append_sim_log(ext, f"[SIM] 멀티 시뮼 시작 (채널={n_ch}, 화면별 스냅샷)")
        except Exception:
            pass

    # --- 프리런(오프라인) → 타임라인 재생 모드 ---
    # 요구사항:
    # - 사용자가 Start를 누르면 UI는 기존과 동일하게 "시뮬이 진행"되는 것처럼 보이되,
    #   내부적으로는 먼저 가능한 빠르게 시뮬을 끝까지 계산(prerun)하고 그 결과를 재생한다.
    # - 이 모드에서는 실시간 tick thread를 띄우지 않는다.
    tick_state = {"count": 0}
    stop_evt = threading.Event()
    ext._sim_thread_stop = stop_evt
    try:
        ext._sim_tick_threads = []
    except Exception:
        pass
    speed_value = max(0.1, ext._sim_speed_model.get_value_as_float())
    _append_sim_log(ext, "[SIM] 프리런 시작: 내부적으로 전체 시뮬을 먼저 계산합니다...")
    # SIM 현황(이력) 영역은 타임테이블 전용으로 유지
    try:
        ext._sim_history_timetable_only = True
    except Exception:
        pass
    try:
        ext._sim_log_ui_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
            lambda e: _drain_sim_log_queue(ext),
            name="morph.tbs_control_2:sim_log_ui_drain",
        )
    except Exception as e:
        _append_sim_log(ext, f"[SIM UI] 로그 큐 드레인 구독 실패: {e}")

    # prerun 결과/플레이어 상태 초기화
    try:
        ext._sim_prerun_done_evt = threading.Event()
        ext._sim_prerun_results_by_screen = None
        ext._sim_playback_player = None
        ext._sim_playback_ui_sub = None
        ext._sim_prerun_timetable_printed = False
    except Exception:
        pass

    def _prerun_thread_body(run_gen: int) -> None:
        try:
            engs = []
            # 단일은 ext._sim_engine, 멀티는 ext._sim_engines 를 사용
            eng_list_outer = getattr(ext, "_sim_engines", None)
            if isinstance(eng_list_outer, list) and len(eng_list_outer) > 0:
                engs = list(eng_list_outer)
            else:
                e0 = getattr(ext, "_sim_engine", None)
                engs = [e0] if e0 is not None else []

            results: Dict[int, SimPreRunResult] = {}
            for idx, eng in enumerate(engs):
                if eng is None:
                    continue
                scr = idx + 1
                # 세대가 바뀌었으면 중단
                try:
                    if int(getattr(ext, "_sim_run_gen", 0) or 0) != int(run_gen):
                        return
                except Exception:
                    pass
                try:
                    res = prerun_engine_to_timeline(screen=scr, engine=eng)
                    results[int(scr)] = res
                except Exception:
                    continue
            try:
                # 세대가 바뀌었으면 버린다
                if int(getattr(ext, "_sim_run_gen", 0) or 0) != int(run_gen):
                    return
            except Exception:
                pass
            try:
                ext._sim_prerun_results_by_screen = results
            except Exception:
                pass
        finally:
            try:
                ev = getattr(ext, "_sim_prerun_done_evt", None)
                if ev is not None:
                    ev.set()
            except Exception:
                pass

    try:
        run_gen = int(getattr(ext, "_sim_run_gen", 0) or 0)
    except Exception:
        run_gen = 0
    th_pr = threading.Thread(
        target=_prerun_thread_body,
        args=(run_gen,),
        name="morph.tbs_control_2.sim_prerun",
        daemon=True,
    )
    ext._sim_thread = th_pr
    th_pr.start()
    return

    def _tick_loop():
        """
        단일 스레드 시뮼 tick 루프(분할 1 또는 레거시 경로).

        - ``ext._sim_engines`` 가 비어 있지 않으면 모든 엔진에 동일 ``scaled`` 로 순차 ``tick``(구조상 한 스레드).
        - ``_sim_tick_pause_event`` 가 켜지면 **전역**으로 tick 을 멈출 수 있다(멀티 N>1 은 별도 worker 사용).
        - ``confirm_each`` + ``_sim_gate_pause_event`` 이면 게이트 확인 전까지 이 루프가 대기한다.
        """
        try:
            print("[SIM] tick thread 시작", flush=True)
            last = time.perf_counter()
            while not stop_evt.is_set():
                # 애니메이션이 재생 중이면 sim tick을 일시정지
                pause_evt = getattr(ext, "_sim_tick_pause_event", None)
                gate_pause_evt = getattr(ext, "_sim_gate_pause_event", None)
                try:
                    confirm_each = bool(getattr(ext, "_sim_confirm_each_step_model", None) is not None and ext._sim_confirm_each_step_model.get_value_as_bool())
                except Exception:
                    confirm_each = False
                if not confirm_each and gate_pause_evt is not None and gate_pause_evt.is_set():
                    try:
                        gate_pause_evt.clear()
                    except Exception:
                        pass
                if confirm_each and gate_pause_evt is not None and gate_pause_evt.is_set():
                    time.sleep(0.02)
                    continue
                if (not _is_multi_viewport_sim(ext)) and pause_evt is not None and pause_evt.is_set():
                    # 원칙: JSON 애니메이션이 실제로 진행 중이면(sim 모듈의 활성 상태가 있으면) 절대 tick 재개하지 않는다.
                    try:
                        anim_running = bool(
                            is_translate_animation_running()
                            or is_rotate_animation_running()
                            or is_curve_animation_running()
                            or (getattr(ext, "_sim_runner", None) is not None and getattr(ext._sim_runner, "is_running", lambda: False)())
                        )
                    except Exception:
                        anim_running = True
                    if anim_running:
                        time.sleep(0.02)
                        continue

                    # fail-safe: 추정 시간이 남아있으면 최소한 그동안은 pause 유지
                    until_wall = getattr(ext, "_sim_tick_pause_until_wall", None)
                    if isinstance(until_wall, (float, int)) and time.monotonic() < float(until_wall):
                        time.sleep(0.02)
                        continue

                    time.sleep(0.02)
                    continue
                eng_list = getattr(ext, "_sim_engines", None)
                if isinstance(eng_list, list) and len(eng_list) > 0:
                    now = time.perf_counter()
                    dt = now - last
                    last = now
                    dt = max(0.001, min(dt, 0.1))
                    scaled = dt * speed_value
                    for sim in eng_list:
                        if sim is not None:
                            try:
                                sim.tick(scaled)
                            except Exception:
                                pass
                    tick_state["count"] += 1
                    if tick_state["count"] == 1:
                        print("[SIM] tick 동작 확인 (first tick, multi)", flush=True)
                    if all(getattr(s, "is_done", False) for s in eng_list if s is not None):
                        print("[SIM] 멀티 종료 감지", flush=True)
                        _enqueue_control_action(ext, SimUiControlAction.EXPORT_XLSX.value)
                        break
                else:
                    sim = getattr(ext, "_sim_engine", None)
                    if sim is None:
                        break
                    now = time.perf_counter()
                    dt = now - last
                    last = now
                    dt = max(0.001, min(dt, 0.1))
                    sim.tick(dt * speed_value)
                    tick_state["count"] += 1
                    if tick_state["count"] == 1:
                        print("[SIM] tick 동작 확인 (first tick)", flush=True)
                    if sim.is_done:
                        print("[SIM] 종료 감지", flush=True)
                        _enqueue_control_action(ext, SimUiControlAction.EXPORT_XLSX.value)
                        break
                time.sleep(0.02)
        except Exception as err:
            # 원인 파악을 위해 traceback까지 출력한다.
            try:
                import traceback

                print(f"[SIM] tick thread 예외: {err}", flush=True)
                print(traceback.format_exc(), flush=True)
            except Exception:
                print(f"[SIM] tick thread 예외: {err}", flush=True)

    th = threading.Thread(target=_tick_loop, name="morph.tbs_control_2.sim_tick", daemon=True)
    ext._sim_thread = th
    th.start()


def _restore_sim_prim_motion_to_initial(ext: Any) -> None:
    """시뮬 **시작·리셋** 시 MOVE·ROTATE·FOUP·USD_TIMELINE prim 을 초기 자세로."""
    paths_seen: set[str] = set()
    paths: List[str] = []

    def _add(path: str) -> None:
        p = str(path or "").strip()
        if p.startswith("/") and p not in paths_seen:
            paths_seen.add(p)
            paths.append(p)

    try:
        from . import port_lot_visibility as _plv

        for p in (_plv.load_port_lot_prim_paths() or {}).values():
            _add(str(p))
    except Exception:
        pass

    try:
        from .tbs_lam_sequence_engine import _collect_prim_paths_for_reset

        runners = getattr(ext, "_sim_runners_by_screen", None)
        if isinstance(runners, dict):
            for r in runners.values():
                if r is not None:
                    for p in _collect_prim_paths_for_reset(
                        getattr(r, "_lam_last_steps", None) or []
                    ):
                        _add(p)
        r0 = getattr(ext, "_sim_runner", None)
        if r0 is not None:
            for p in _collect_prim_paths_for_reset(
                getattr(r0, "_lam_last_steps", None) or []
            ):
                _add(p)
    except Exception:
        pass

    try:
        reg = getattr(ext, "_tbs_registry", None)
        if reg is not None and hasattr(reg, "all_instances"):
            for inst in reg.all_instances():
                _add(str(getattr(inst, "prim_path", "") or ""))
    except Exception:
        pass

    try:
        from . import tbs_lam_rotate_animation as _lrx
        from . import tbs_lam_translate_animation as _ltx

        _ltx.stop_all_translate_animations()
        _lrx.stop_all_rotate_animations()
    except Exception:
        pass
    try:
        stop_all_translate_animations(preserve_foup_port_lot_prims=False)
        stop_all_rotate_animations()
        stop_all_curve_animations()
    except Exception:
        pass

    try:
        from . import port_lot_visibility as _plv

        _plv.clear_foup_in_progress()
        _plv.clear_foup_lifted()
        _plv.restore_port_lot_prims_to_authoring()
    except Exception:
        pass

    try:
        sch = getattr(ext, "_tbs_scheduler", None)
        stop_fn = getattr(sch, "stop_all", None) if sch is not None else None
        if callable(stop_fn):
            stop_fn()
    except Exception:
        pass

    def _do_on_main() -> None:
        if paths:
            from .tbs_lam_sequence_engine import _reset_tbs_offset_ops_for_paths

            _reset_tbs_offset_ops_for_paths(paths)

        try:
            from .tbs_lam_sequence_editor import _range_start_seconds_for_instance

            reg = getattr(ext, "_tbs_registry", None)
            ev = getattr(ext, "_tbs_evaluator", None)
            if reg is not None and hasattr(reg, "all_instances"):
                for inst in reg.all_instances():
                    pp = str(getattr(inst, "prim_path", "") or "").strip()
                    if not pp.startswith("/"):
                        continue
                    try:
                        inst.virtual_time = _range_start_seconds_for_instance(inst)
                        inst.state = "stopped"
                    except Exception:
                        pass
                    if ev is not None:
                        for fn_name in (
                            "end_replay_mode",
                            "end_master_timeline_mode",
                            "invalidate_mapping",
                            "force_rebuild_attr_cache",
                        ):
                            fn = getattr(ev, fn_name, None)
                            if callable(fn):
                                try:
                                    fn(pp)
                                except Exception:
                                    pass
        except Exception:
            pass

        try:
            usd_animation_control.stop_usd_animation(None)
            usd_animation_control.reset_timeline_to_zero(None)
        except Exception:
            pass

    try:
        if threading.current_thread() is threading.main_thread():
            _do_on_main()
        else:
            from .tbs_lam_sequence_engine import _dispatch_main_wait

            _dispatch_main_wait(_do_on_main, timeout=20.0)
    except Exception as exc:
        print(f"[TBS/SIM] restore motion failed: {exc}", flush=True)


def on_sim_stop_clicked(ext: Any) -> None:
    """
    시뮬레이션 중지(Stop).

    목표(요구사항):
    - stop/reset 후 "다음 스텝에서 다시 이어서 진행되는" 잔여 실행을 방지한다.
    - 멀티 화면에서 화면별 runner/큐/인터럽트/일시정지(pause) 상태가 남아
      다음 실행이 깨지는 회귀(애니가 있는데 재생이 안 됨, 진행률 0% 교착 등)를 방지한다.

    주요 동작 요약(추적용):
    - (세대 토큰) ``ext._sim_run_gen`` 증가:
      - UI 큐/애니 이벤트 처리부가 이전 실행의 payload를 무시하도록 만드는 "세대" 구분자.
    - (엔진 stop) ``TBSSimulationEngine.stop()`` 호출:
      - 단일 엔진: ``ext._sim_engine.stop()``
      - 멀티 엔진: ``ext._sim_engines[*].stop()``
      - 엔진 내부에서는 simpy 프로세스 종료, 진행 emit 중단, 최종 상태 정리 등이 수행된다.
    - (UI 업데이트 detach) ``_detach_sim_update(ext)``:
      - 엔진/워커가 올리던 UI 업데이트 subscription(로그/진행현황 drain)을 해제한다.
    - (화면별 애니 상태 정리) ``_sim_runners_by_screen`` 및 아래 dict들을 전부 초기화/clear:
      - pending/active: ``_sim_anim_pending_by_screen``, ``_sim_anim_active_by_screen``
      - pause: ``_sim_tick_pause_events_by_screen``, ``_sim_tick_pause_until_wall_by_screen``
      - interrupt(공정시간우선): ``_sim_interrupt_anim_event_by_screen``
    - (게이트/일시정지 해제) ``_sim_tick_pause_event``, ``_sim_gate_pause_event`` clear +
      공정확인 다이얼로그(``_sim_gate_dialog``) 강제 destroy
    - (EP 타임라인 리셋) 그래프 상태/위젯 파기:
      - state dict: ``_sim_ep_timeline_state_by_screen``, ``_sim_ep_occ_timeline_state_by_screen``,
        ``_sim_last_ports_occupancy_by_screen``
      - (레거시) 혹시 남아있으면 ``_sim_ep_timeline_ui_sub`` unsubscribe
      - widget: 채널별 ``ep_timeline_widget`` destroy
    """
    # stop/reset 이후 남아있는 큐 아이템을 무시하기 위한 세대 토큰 증가
    try:
        ext._sim_run_gen = int(getattr(ext, "_sim_run_gen", 0) or 0) + 1
    except Exception:
        ext._sim_run_gen = 1
    # FOUP 진행중 보호 표시 정리(안전망):
    # - END 후 1초 unmark 가 잡히기 전에 stop 이 들어오면 보호 플래그가 남을 수 있다.
    # - 보류 중인 unmark subscription 도 함께 해제하여 잔여 콜백을 차단한다.
    try:
        from . import port_lot_visibility as _plv  # type: ignore
        _plv.clear_foup_in_progress()
    except Exception:
        pass
    try:
        holders = getattr(ext, "_foup_unmark_subs", None)
        if isinstance(holders, list):
            for h in holders:
                try:
                    s = h.get("sub") if isinstance(h, dict) else None
                    if s is not None:
                        s.unsubscribe()
                except Exception:
                    pass
                try:
                    if isinstance(h, dict):
                        h["done"] = True
                        h["sub"] = None
                except Exception:
                    pass
            ext._foup_unmark_subs = []
    except Exception:
        pass
    # FOUP 라벨 idle 리셋 + 보류 중인 라벨 reset sub 정리(안전망):
    # - 진행 중이던 라벨이 'DONE/대기' 갱신 없이 stop 이 들어오면 텍스트가 잔여로 남을 수 있다.
    # - 보류된 1회성 update sub 도 모두 unsubscribe 해 잔여 콜백을 차단한다.
    try:
        holders2 = getattr(ext, "_foup_label_reset_subs", None)
        if isinstance(holders2, list):
            for h in holders2:
                try:
                    s = h.get("sub") if isinstance(h, dict) else None
                    if s is not None:
                        s.unsubscribe()
                except Exception:
                    pass
                try:
                    if isinstance(h, dict):
                        h["done"] = True
                        h["sub"] = None
                except Exception:
                    pass
            ext._foup_label_reset_subs = []
    except Exception:
        pass
    try:
        _reset_all_foup_labels(ext)
    except Exception:
        pass
    # FOUP material 안전망: 정상 종료가 아니어도 다음 실행 시작 상태가 깨끗하도록
    # 모든 매핑 prim 의 material 을 기본(phong1)으로 일괄 복원한다.
    # 분할화면(보조 USD 컨텍스트)도 함께 처리한다.
    try:
        from . import port_lot_visibility as _plv  # type: ignore
        # 기본 컨텍스트
        try:
            _plv.restore_port_lot_prims_to_default_material(None)
        except Exception:
            pass
        # 분할화면 보조 컨텍스트들
        try:
            ctx_names = list(getattr(ext, "_sim_multi_context_names", []) or [])
        except Exception:
            ctx_names = []
        for cn in ctx_names:
            try:
                _plv.restore_port_lot_prims_to_default_material(str(cn))
            except Exception:
                pass
    except Exception:
        pass
    # 프리런/재생 모드 정리
    try:
        p = getattr(ext, "_sim_playback_player", None)
        if p is not None and hasattr(p, "stop"):
            try:
                p.stop()
            except Exception:
                pass
    except Exception:
        pass
    try:
        sub = getattr(ext, "_sim_playback_ui_sub", None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        ext._sim_playback_ui_sub = None
    except Exception:
        pass
    try:
        ext._sim_playback_started = False
    except Exception:
        pass
    try:
        ev = getattr(ext, "_sim_prerun_done_evt", None)
        if ev is not None and hasattr(ev, "clear"):
            ev.clear()
        ext._sim_prerun_results_by_screen = None
    except Exception:
        pass
    for eng in list(getattr(ext, "_sim_engines", None) or []):
        if eng is None:
            continue
        try:
            eng.stop()
        except Exception:
            pass
    try:
        ext._sim_engines = []
    except Exception:
        pass
    sim = getattr(ext, "_sim_engine", None)
    if sim is not None:
        try:
            sim.stop()
        except Exception:
            pass
    _detach_sim_update(ext)
    # 화면별 runner/큐/상태를 모두 정리해야 stop/reset 후 재시작에서
    # "애니가 있는데도 재생이 안 되는" 잔여 상태(구독/타이머/인터럽트) 회귀를 막을 수 있다.
    try:
        runners = getattr(ext, "_sim_runners_by_screen", None)
        if isinstance(runners, dict):
            for r in list(runners.values()):
                try:
                    if r is not None:
                        r.pause()
                except Exception:
                    pass
    except Exception:
        pass
    runner = getattr(ext, "_sim_runner", None)
    if runner is not None:
        try:
            runner.pause()
        except Exception:
            pass
    # pause 상태 해제
    pe = getattr(ext, "_sim_tick_pause_event", None)
    if pe is not None:
        try:
            pe.clear()
        except Exception:
            pass
    ge = getattr(ext, "_sim_gate_pause_event", None)
    if ge is not None:
        try:
            ge.clear()
        except Exception:
            pass
    # 공정확인 창이 열려있으면 강제 종료(리셋/중지 시 다음 실행에 pause가 남지 않게)
    try:
        w = getattr(ext, "_sim_gate_dialog", None)
        if w is not None:
            try:
                w.visible = False
            except Exception:
                pass
            try:
                w.destroy()
            except Exception:
                pass
        ext._sim_gate_dialog = None
    except Exception:
        pass
    try:
        ext._sim_tick_pause_until_wall = None
    except Exception:
        pass
    try:
        ext._sim_anim_pending = []
    except Exception:
        pass
    try:
        pending_by = getattr(ext, "_sim_anim_pending_by_screen", None)
        if isinstance(pending_by, dict):
            for k in list(pending_by.keys()):
                pending_by[k] = []
    except Exception:
        pass
    try:
        active_by = getattr(ext, "_sim_anim_active_by_screen", None)
        if isinstance(active_by, dict):
            for k in list(active_by.keys()):
                active_by[k] = {}
    except Exception:
        pass
    try:
        until_by = getattr(ext, "_sim_tick_pause_until_wall_by_screen", None)
        if isinstance(until_by, dict):
            for k in list(until_by.keys()):
                until_by[k] = None
    except Exception:
        pass
    try:
        pause_by = getattr(ext, "_sim_tick_pause_events_by_screen", None)
        if isinstance(pause_by, dict):
            for ev in list(pause_by.values()):
                try:
                    if ev is not None:
                        ev.clear()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        ie_by = getattr(ext, "_sim_interrupt_anim_event_by_screen", None)
        if isinstance(ie_by, dict):
            for ev in list(ie_by.values()):
                try:
                    if ev is not None:
                        ev.clear()
                except Exception:
                    pass
    except Exception:
        pass
    # EP 타임라인 그래프 상태/위젯 초기화(리셋/정지 후 누적 잔상 방지)
    try:
        ext._sim_ep_timeline_state_by_screen = {}
    except Exception:
        pass
    try:
        ext._sim_ep_occ_timeline_state_by_screen = {}
    except Exception:
        pass
    try:
        ext._sim_last_ports_occupancy_by_screen = {}
    except Exception:
        pass
    try:
        sub = getattr(ext, "_sim_ep_timeline_ui_sub", None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        ext._sim_ep_timeline_ui_sub = None
    except Exception:
        pass
    try:
        ext._sim_ep_timeline_virtual_time_by_screen = {}
    except Exception:
        pass
    try:
        chans = getattr(ext, "_sim_monitor_channels", None)
        if isinstance(chans, list):
            for ch in chans:
                if not isinstance(ch, dict):
                    continue
                w = ch.get("progress_ep_timeline_widget", None)
                if w is not None:
                    try:
                        w.destroy()
                    except Exception:
                        pass
                    ch["progress_ep_timeline_widget"] = None
                w2 = ch.get("ep_timeline_widget", None)
                if w2 is not None:
                    try:
                        w2.destroy()
                    except Exception:
                        pass
                    ch["ep_timeline_widget"] = None
    except Exception:
        pass
    try:
        ext._sim_engines = []
    except Exception:
        pass


def on_sim_reset_clicked(ext: Any) -> None:
    """
    시뮬레이션 리셋(Reset).

    Stop과의 차이(추적용):
    - 먼저 ``on_sim_stop_clicked(ext)``를 호출하여 "실행 중인 것"을 완전히 끊는다.
    - 그 다음, UI 텍스트/포트 박스/그래프 등을 "초기 화면" 상태로 되돌린다.
    - 추가로, 포트 LOT authoring 캐시를 clear 하여 다음 실행에서 경로/프림/표시가
      이전 실행의 잔여 데이터에 영향을 받지 않게 한다.

    이 함수가 만지는 대표 상태:
    - 엔진/러너 참조: ``ext._sim_engine = None`` 및 ``ext._sim_engines = []``
    - UI 텍스트: history/progress/port_state 라벨 텍스트 초기화
    - 포트 박스: 각 포트를 '-'로 표기하고 스타일을 초기화
    - EP 타임라인: t=0.0 초기 렌더 + 관련 dict를 완전 초기화
    """
    on_sim_stop_clicked(ext)
    try:
        _restore_sim_prim_motion_to_initial(ext)
    except Exception:
        pass
    try:
        clear_port_lot_authoring_cache()
    except Exception:
        pass
    ext._sim_engine = None
    try:
        ext._sim_engines = []
    except Exception:
        pass
    if getattr(ext, "_sim_history_text", None):
        ext._sim_history_text.set_value("[SIM] 리셋 완료")
    if getattr(ext, "_sim_progress_text", None):
        ext._sim_progress_text.set_value("[진행현황] 없음")
    if getattr(ext, "_sim_port_state_text", None):
        ext._sim_port_state_text.set_value("[포트상태] 없음")
    if getattr(ext, "_sim_port_state_label", None) is not None:
        ext._sim_port_state_label.text = "[포트상태] 없음"
    chans_r = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans_r, list) and len(chans_r) > 1:
        for ch in chans_r:
            if not isinstance(ch, dict):
                continue
            try:
                si = int(ch.get("screen", 1))
            except Exception:
                si = 1
            ht = "[SIM] 리셋 완료" if si == 1 else f"[SIM·화면{si}] 리셋 완료"
            pt = "[진행현황] 없음" if si == 1 else f"[진행현황·화면{si}] 없음"
            ph = f"[포트상태·화면{si}] 없음"
            hl = ch.get("history_label")
            pl = ch.get("progress_label")
            phdr = ch.get("port_header")
            if hl is not None:
                hl.text = ht
            if pl is not None:
                pl.text = pt
            if phdr is not None:
                phdr.text = ph
            cells = ch.get("port_cells") or {}
            boxes = ch.get("port_cell_boxes") or {}
            for port in ("INOUT", "BP2", "BP3", "BP4", "BP1", "EP1", "EP2"):
                if port in cells:
                    cells[port].text = "IN/OUT:-" if port == "INOUT" else f"{port}:-"
                try:
                    _set_port_box_style(ext, port, "-", boxes)
                except Exception:
                    pass
            ep3c = ch.get("port_ep3_cell")
            if ep3c is not None:
                ep3c.text = "EP3:-"
            try:
                _set_port_box_style(ext, "EP3", "-", boxes)
            except Exception:
                pass
            # 멀티 채널도 EP 타임라인을 즉시 t=0으로 재렌더(총시간 라벨 잔상 방지)
            try:
                occ0 = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
                by_occ = getattr(ext, "_sim_last_ports_occupancy_by_screen", None)
                if not isinstance(by_occ, dict):
                    by_occ = {}
                    ext._sim_last_ports_occupancy_by_screen = by_occ
                by_occ[str(si)] = dict(occ0)
                _update_ep_timeline_under_port_state(ext, ch, occ0, "0.0")
            except Exception:
                pass
    else:
        if getattr(ext, "_sim_history_label", None) is not None:
            ext._sim_history_label.text = "[SIM] 리셋 완료"
        if getattr(ext, "_sim_progress_label", None) is not None:
            ext._sim_progress_label.text = "[진행현황] 없음"
        if getattr(ext, "_sim_port_state_header_label", None) is not None:
            ext._sim_port_state_header_label.text = "[포트상태] 없음"
        cells = getattr(ext, "_sim_port_cells", {}) or {}
        for port in ("INOUT", "BP2", "BP3", "BP4", "BP1", "EP1", "EP2"):
            if port in cells:
                cells[port].text = "IN/OUT:-" if port == "INOUT" else f"{port}:-"
            try:
                _set_port_box_style(ext, port, "-")
            except Exception:
                pass
        if getattr(ext, "_sim_port_ep3_cell", None) is not None:
            ext._sim_port_ep3_cell.text = "EP3:-"
        try:
            _set_port_box_style(ext, "EP3", "-")
        except Exception:
            pass
        # 단일 채널 EP 타임라인 초기 렌더(t=0.0)
        try:
            occ0 = {k: "" for k in ("INOUT", "BP1", "BP2", "BP3", "BP4", "EP1", "EP2", "EP3")}
            ext._sim_last_ports_occupancy_by_screen["1"] = dict(occ0)
            # 단일 채널은 channels[0]이거나 ext 레거시 참조이므로, 가능하면 channels에서 가져온다.
            chans_s = getattr(ext, "_sim_monitor_channels", None)
            if isinstance(chans_s, list) and len(chans_s) >= 1 and isinstance(chans_s[0], dict):
                _update_ep_timeline_under_port_state(ext, chans_s[0], occ0, "0.0")
        except Exception:
            pass
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}
    # EP 타임라인도 완전 초기화
    try:
        ext._sim_ep_timeline_state_by_screen = {}
    except Exception:
        pass
    try:
        ext._sim_ep_occ_timeline_state_by_screen = {}
    except Exception:
        pass
    try:
        ext._sim_last_ports_occupancy_by_screen = {}
    except Exception:
        pass
    # 총시간(끝 라벨) 캐시도 초기화(리셋 후 이전 총시간이 남는 문제 방지)
    try:
        ext._sim_last_total_est_by_screen = {}
    except Exception:
        pass
    # 진행현황(텍스트용) 마지막 payload도 초기화(리셋 후 DONE/총시간 잔상 방지)
    try:
        ext._sim_progress_last_payload_by_screen = {}
    except Exception:
        pass
    # 프리런/재생 상태도 초기화(리셋 후 이전 결과 잔상 방지)
    try:
        ext._sim_prerun_results_by_screen = None
        ext._sim_playback_player = None
        ext._sim_playback_started = False
        ext._sim_playback_done = False
    except Exception:
        pass
    try:
        chans = getattr(ext, "_sim_monitor_channels", None)
        if isinstance(chans, list):
            for ch in chans:
                if not isinstance(ch, dict):
                    continue
                w2 = ch.get("ep_timeline_widget", None)
                if w2 is not None:
                    try:
                        w2.destroy()
                    except Exception:
                        pass
                    ch["ep_timeline_widget"] = None
    except Exception:
        pass
    try:
        sub = getattr(ext, "_sim_ep_timeline_ui_sub", None)
        if sub is not None:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        ext._sim_ep_timeline_ui_sub = None
    except Exception:
        pass
    try:
        ext._sim_ep_timeline_virtual_time_by_screen = {}
    except Exception:
        pass
    # 최근 요약/대기 토큰 초기화
    try:
        ext._sim_recent_story_blocks = []
    except Exception:
        pass
    # (요청으로 제거) 점 표시 기능 비활성화


def on_sim_log_view_changed(ext: Any) -> None:
    # 표시모드 제거: 항상 둘다(진행현황+이력로그)
    chans = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans, list) and len(chans) > 0:
        for ch in chans:
            if not isinstance(ch, dict):
                continue
            pf = ch.get("progress_frame")
            hf = ch.get("history_frame")
            if pf is not None:
                try:
                    pf.visible = True
                except Exception:
                    pass
            if hf is not None:
                try:
                    hf.visible = True
                except Exception:
                    pass
        return
    if getattr(ext, "_sim_progress_frame", None) is not None:
        ext._sim_progress_frame.visible = True
    if getattr(ext, "_sim_history_frame", None) is not None:
        ext._sim_history_frame.visible = True


def on_copy_sim_progress(ext: Any) -> None:
    # 확장: 진행현황 + Sim 로그(이력) 함께 복사
    progress = ""
    history = ""
    chans = getattr(ext, "_sim_monitor_channels", None)
    if isinstance(chans, list) and len(chans) > 1:
        pb: List[str] = []
        hb: List[str] = []
        for ch in chans:
            if not isinstance(ch, dict):
                continue
            try:
                si = int(ch.get("screen", 0))
            except Exception:
                si = 0
            pl = ch.get("progress_label")
            hl = ch.get("history_label")
            if pl is not None:
                pt = str(pl.text or "").strip()
                if pt:
                    pb.append(f"=== 화면{si} 진행현황 ===\n{pt}")
            if hl is not None:
                ht = str(hl.text or "").strip()
                if ht:
                    hb.append(f"=== 화면{si} Sim 로그 ===\n{ht}")
        progress = "\n\n".join(pb).strip()
        history = "\n\n".join(hb).strip()
    else:
        if getattr(ext, "_sim_progress_label", None) is not None:
            progress = ext._sim_progress_label.text or ""
        if not progress.strip() and getattr(ext, "_sim_progress_text", None):
            progress = ext._sim_progress_text.as_string or ""
        if getattr(ext, "_sim_history_label", None) is not None:
            history = ext._sim_history_label.text or ""
        if not history.strip() and getattr(ext, "_sim_history_text", None):
            history = ext._sim_history_text.as_string or ""

    text = (progress or "").strip()
    if history.strip():
        text = (text + "\n\n" if text else "") + "[Sim 로그]\n" + history.strip()
    if not text.strip():
        _append_sim_log(ext, "[SIM UI] 복사할 진행현황/Sim 로그가 없습니다.")
        return
    try:
        import omni.kit.clipboard as cb  # type: ignore
        if hasattr(cb, "copy"):
            cb.copy(text)
        elif hasattr(cb, "set_text"):
            cb.set_text(text)
        else:
            raise RuntimeError("clipboard api not found")
        _append_sim_log(ext, "[SIM UI] 진행현황+Sim 로그 복사 완료")
    except Exception:
        print("[SIM UI] 클립보드 미지원: 텍스트를 콘솔에 출력합니다.", flush=True)
        print(text, flush=True)
        _append_sim_log(ext, "[SIM UI] 클립보드 미지원으로 콘솔 출력")


def on_sim_ep_count_changed(ext: Any) -> None:
    try:
        idx = ext._sim_ep_count_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    is_ep3 = idx == 1

    # 요구사항: EP 포트 개수는 "현재 화면 설정 저장" 시점에 해당 화면 스냅샷에 반영되어야 한다.
    # - 화면1은 기본값이므로 즉시 반영(스냅샷[0] 갱신) 가능
    # - 화면2~4 스냅샷은 여기서 건드리지 않는다(저장 버튼에서만 반영)
    try:
        snaps = list(getattr(ext, "_sim_per_screen_snapshots", None) or [None, None, None, None])
    except Exception:
        snaps = [None, None, None, None]
    while len(snaps) < 4:
        snaps.append(None)
    snaps = snaps[:4]
    try:
        if isinstance(snaps[0], dict):
            snaps[0]["ep_count_idx"] = int(idx)
        # 화면1 스냅샷이 아직 없으면 생성(기본값 성격)
        elif snaps[0] is None:
            cap0 = _capture_per_screen_sim_settings(ext)
            if isinstance(cap0, dict):
                cap0["ep_count_idx"] = int(idx)
            snaps[0] = cap0
        ext._sim_per_screen_snapshots = snaps
    except Exception:
        pass

    if getattr(ext, "_sim_init_bp4_row", None) is not None:
        ext._sim_init_bp4_row.visible = is_ep3
    if not is_ep3 and getattr(ext, "_sim_init_bp4_model", None) is not None:
        ext._sim_init_bp4_model.set_value(False)
    if getattr(ext, "_sim_init_ep3_row", None) is not None:
        ext._sim_init_ep3_row.visible = is_ep3
    if not is_ep3 and getattr(ext, "_sim_init_ep3_model", None) is not None:
        ext._sim_init_ep3_model.set_value(False)
    # 고장 포트 행도 동일 규칙 적용
    if getattr(ext, "_sim_fault_bp4_row", None) is not None:
        ext._sim_fault_bp4_row.visible = is_ep3
    if not is_ep3 and getattr(ext, "_sim_fault_bp4_model", None) is not None:
        ext._sim_fault_bp4_model.set_value(False)
    if getattr(ext, "_sim_fault_ep3_row", None) is not None:
        ext._sim_fault_ep3_row.visible = is_ep3
    if not is_ep3 and getattr(ext, "_sim_fault_ep3_model", None) is not None:
        ext._sim_fault_ep3_model.set_value(False)
    _sync_ep3_port_cell_visibility(ext)
    try:
        from .tbs_ep_port_visibility import on_sim_ep_count_combo_changed

        on_sim_ep_count_combo_changed(ext)
    except Exception:
        pass


def receive_signal_data(ext: Any, data: str, format: str = "json") -> bool:
    parsed = parse_signal(data, format)
    if not parsed:
        return False
    run_generator_from_parsed(ext, parsed)
    return True


def run_generator_from_parsed(ext: Any, parsed: dict) -> None:
    stage = get_stage()
    if not stage:
        return
    objects = parsed.get("objects") or []
    segments = parsed.get("segments") or []
    if not objects or not segments:
        return
    for name in objects:
        if not isinstance(name, str):
            continue
        paths = find_all_prim_paths_by_name(stage, name)
        for path in paths:
            if not path:
                continue
            stop_prim_translate_animation(path)
            stop_prim_curve_animation(path)
            run_prim_translate_animation(path, segments, loop=False)


def on_refresh_prim_list(ext: Any) -> None:
    stage = get_stage()
    if not stage:
        return
    ext._tracked_paths = collect_prim_paths_safe(stage)
    refresh_object_list(ext)


def refresh_object_list(ext: Any) -> None:
    if ext._object_list_frame is None:
        return
    ext._object_list_frame.clear()
    stage = get_stage()
    if not stage:
        with ext._object_list_frame:
            ui.Label("USD를 먼저 로드하세요.")
        return

    def _valid_path(p: str) -> bool:
        try:
            return stage.GetPrimAtPath(p).IsValid()
        except (UnicodeDecodeError, UnicodeEncodeError):
            return False

    valid_paths = [p for p in ext._tracked_paths if _valid_path(p)]
    total = len(valid_paths)
    priority_prefix = (ext._priority_prefix_model.get_value_as_string().strip() or "")

    if priority_prefix:
        priority_paths: List[str] = []
        rest_paths: List[str] = []
        for p in valid_paths:
            try:
                prim = stage.GetPrimAtPath(p)
                if not prim or not prim.IsValid():
                    rest_paths.append(p)
                    continue
                name = safe_str(prim.GetName())
                if name.startswith(priority_prefix):
                    priority_paths.append(p)
                else:
                    rest_paths.append(p)
            except Exception:
                rest_paths.append(p)
        need = max(0, MAX_PRIMS_DISPLAY - len(priority_paths))
        display_paths = priority_paths[:MAX_PRIMS_DISPLAY] + rest_paths[:need]
    else:
        display_paths = valid_paths[:MAX_PRIMS_DISPLAY]

    with ext._object_list_frame:
        if total > MAX_PRIMS_DISPLAY:
            ui.Label(f"총 {total}개 prim 중 {len(display_paths)}개만 표시됩니다.", height=0)
            ui.Spacer(height=4)
        if priority_prefix:
            n_priority = min(len(priority_paths), MAX_PRIMS_DISPLAY)
            n_rest = len(display_paths) - n_priority
            ui.Label(f"접두사 '{priority_prefix}' 우선: {n_priority}개, 나머지 순서대로 {n_rest}개", height=0)
            ui.Spacer(height=4)
        for idx, prim_path in enumerate(display_paths):
            build_object_panel(ext, ext._object_list_frame, prim_path, idx + 1)


def build_object_panel(ext: Any, parent: ui.VStack, prim_path: str, index: int) -> None:
    try:
        stage = get_stage()
        prim = stage.GetPrimAtPath(prim_path) if stage else None
        if not prim or not prim.IsValid():
            return
        title = get_prim_display_name(prim, index)
        local = get_prim_local_translate(prim)
        pos_models = [
            ui.SimpleFloatModel(local[0]),
            ui.SimpleFloatModel(local[1]),
            ui.SimpleFloatModel(local[2]),
        ]

        def update_prim_position():
            s = get_stage()
            p = s.GetPrimAtPath(prim_path) if s else None
            if p and p.IsValid():
                set_prim_translate_only(p, Gf.Vec3f(
                    pos_models[0].get_value_as_float(),
                    pos_models[1].get_value_as_float(),
                    pos_models[2].get_value_as_float(),
                ))

        with parent:
            with ui.CollapsableFrame(title, collapsed=False):
                with ui.VStack(spacing=6):
                    ui.Label("Position (X, Y, Z)", height=0)
                    with ui.HStack():
                        for i, label in enumerate(["X", "Y", "Z"]):
                            ui.Label(label, width=24)
                            ui.FloatField(model=pos_models[i])
                    for m in pos_models:
                        m.add_value_changed_fn(lambda _: update_prim_position())
                    ui.Spacer(height=4)
                    ui.Button("3D 정보 보기", height=24, clicked_fn=lambda p=prim_path: show_prim_info_in_viewport(ext, p))
                    ui.Spacer(height=4)
                    with ui.HStack(spacing=8):
                        ui.Button("button_0", width=0, clicked_fn=lambda p=prim_path: on_button_0(ext, p))
                        ui.Button("button_1", width=0, clicked_fn=lambda p=prim_path: on_button_1(ext, p))
                        ui.Button("button_2", width=0, clicked_fn=lambda p=prim_path: on_button_2(ext, p))
    except (UnicodeDecodeError, UnicodeEncodeError):
        return


def on_button_0(ext: Any, prim_path: str) -> None:
    stop_prim_translate_animation(prim_path)
    stop_prim_curve_animation(prim_path)
    stop_prim_rotate_animation(prim_path)
    run_prim_translate_animation(
        prim_path,
        [
            {"duration": 1.0, "delta": (100.0, 0.0, 0.0)},
            {"duration": 1.0, "delta": (0.0, 0.0, 100.0)},
        ],
        loop=False,
    )


def on_button_1(ext: Any, prim_path: str) -> None:
    stop_prim_translate_animation(prim_path)
    stop_prim_curve_animation(prim_path)
    stop_prim_rotate_animation(prim_path)
    stage = get_stage()
    prim = stage.GetPrimAtPath(prim_path) if stage else None
    if not prim or not prim.IsValid():
        return
    start = get_prim_local_translate(prim)
    start_t = (start[0], start[1], start[2])
    end_t = (start[0] + 100.0, start[1], start[2])
    path_points = make_parabolic_path(start=start_t, end=end_t, arc_height=30.0, num_points=24)
    run_prim_curve_animation(prim_path, path_points, duration_sec=1.0, loop=False)


def on_button_2(ext: Any, prim_path: str) -> None:
    stop_prim_translate_animation(prim_path)
    stop_prim_curve_animation(prim_path)
    stop_prim_rotate_animation(prim_path)
    run_prim_rotate_animation(
        prim_path,
        [{"duration": 3.0, "delta": (0.0, 90.0, 0.0)}],
        loop=False,
    )
