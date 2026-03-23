# SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""
control_window.py — TBS 제어창 UI 및 이벤트 핸들러

【역할】
- build_control_window(ext): "TBS 제어창" 창. USD 타임라인(수동/자동), 가상 시그널 샘플,
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

【이벤트→애니메이션(JSON) 상태 기반 룰 매핑 유지보수 가이드】
- 목적: 같은 이벤트라도 from/to/포트 점유 상태가 다르면 다른 JSON을 실행할 수 있게 한다.
- 규칙 파일(우선순위):
  1) `config/event_animation_rules.json`  ← 권장(상태 기반)
  2) `config/event_animation_map.json`    ← 기본 fallback(이벤트 단순 매핑)
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
- 호출 흐름:
  1) simulation_engine._emit_event()에서 payload + ports_occupancy 전달
  2) on_sim_start_clicked()의 on_event 콜백이 handle_sim_event_for_animation(payload) 호출
  3) _resolve_event_animation_entry(seq, payload):
     - 먼저 rules에서 조건 매칭(우선순위 높은 규칙 우선)
     - 없으면 event_animation_map fallback
  4) _execute_mapped_sequence_stub(...)에서 파일 존재/파싱 검증 후 실행 훅 로그 출력
- 시퀀스 편집기 JSON 연결 방법:
  1) 시퀀스 편집기에서 JSON 저장
  2) 파일을 extension 내부 경로(예: data/sim_sequences/*.json)에 배치
  3) rules 또는 map의 use.json 경로에 등록
  4) 시뮬레이션 이벤트 발생 시 자동 매칭/검증 로그 확인

【XML 6종류와 UI 필드】 (로직·상수는 xml_generator.py)
- FROM_PORT_ID + TO_PORT_ID: MOVE_TRANSFERING, MOVE
- PORT_ID만: READYTOLOAD, ARRIVED, READYTOUNLOAD, REMOVED
새 종류 추가 시: xml_generator 수정 + 이 파일의 ComboBox·seqs 3곳 + 필요 시 IntField/모델 추가.

사용처: extension.py on_startup → build_control_window(self)
"""

from typing import Any, Dict, List, Optional, Tuple
import random
import threading
import time
import queue
import json
from pathlib import Path

import omni.kit.app as app
import omni.ui as ui
from pxr import Gf

from . import usd_animation_control
from . import xml_generator
from .curve_animation import make_parabolic_path, run_prim_curve_animation, stop_prim_curve_animation
from .prim_info import get_prim_display_name, safe_str
from .prim_utils import (
    collect_prim_paths_safe,
    find_all_prim_paths_by_name,
    get_prim_local_translate,
    get_stage,
    set_prim_translate_only,
)
from .rotate_animation import run_prim_rotate_animation, stop_prim_rotate_animation
from .selection_overlay import show_prim_info_in_viewport
from .signal_parser import parse_signal
from .simulation_engine import (
    Lot,
    SimulationInitConfig,
    SimulationLogConfig,
    SimulationTimingConfig,
    TBSSimulationEngine,
)
from .translate_animation import run_prim_translate_animation, stop_prim_translate_animation

MAX_PRIMS_DISPLAY = 80
DEFAULT_PRIORITY_NAME_PREFIX = "Mesh_"
SIM_SEQ_ALIAS = {
    "READYTOLOAD": xml_generator.SEQ_READYTOLOAD,
    "ARRIVED": xml_generator.SEQ_ARRIVED,
    "MOVE_TRANSFERING": xml_generator.SEQ_MOVE_TRANSFERING,
    "MOVE": xml_generator.SEQ_MOVE,
    "READYTOUNLOAD": xml_generator.SEQ_READYTOUNLOAD,
    "REMOVED": xml_generator.SEQ_REMOVED,
}
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
    # .../source/extensions/morph.tbs_control_1
    return Path(__file__).resolve().parents[2]


def _event_animation_map_path() -> Path:
    return _extension_root_dir() / "config" / "event_animation_map.json"


def _event_animation_rules_path() -> Path:
    return _extension_root_dir() / "config" / "event_animation_rules.json"


def _load_event_animation_map() -> Dict[str, Any]:
    global _EVENT_ANIM_MAP_CACHE, _EVENT_ANIM_MAP_MTIME
    p = _event_animation_map_path()
    if not p.exists():
        _EVENT_ANIM_MAP_CACHE = {}
        _EVENT_ANIM_MAP_MTIME = None
        return {}


def _load_event_animation_rules() -> List[Dict[str, Any]]:
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


def _resolve_event_animation_entry(seq: str, payload: Optional[Dict[str, str]] = None) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    반환:
    - json_path_str: 실제 JSON 경로 문자열(없으면 None)
    - meta: runner/description 등 부가정보
    """
    # 1) 상태 기반 rules 우선
    j_rule, meta_rule, rule_name = _resolve_rule_entry(seq, payload or {})
    if j_rule:
        return (j_rule, meta_rule, rule_name or "(unnamed-rule)")

    # 2) 기존 단순 map fallback
    m = _load_event_animation_map()
    if not m:
        return (None, None, None)

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
            return (v, {"runner": "sequence_editor", "description": ""}, None)
        if isinstance(v, dict):
            j = v.get("json")
            if isinstance(j, str) and j.strip():
                return (j.strip(), v, None)
    return (None, None, None)


def _normalize_json_path(path_text: str) -> Path:
    p = Path(path_text)
    if p.is_absolute():
        return p
    return (_extension_root_dir() / p).resolve()


def _execute_mapped_sequence_stub(
    seq: str,
    payload: Dict[str, str],
    json_path_text: str,
    meta: Optional[Dict[str, Any]],
    rule_name: Optional[str],
    verbose: bool,
) -> None:
    """
    실제 실행 훅(현재는 준비/검증 로그).
    추후 여기에서 SequenceRunner 호출로 연결한다.
    """
    p = _normalize_json_path(json_path_text)
    runner = str((meta or {}).get("runner", "sequence_editor"))
    desc = str((meta or {}).get("description", ""))
    if not p.exists():
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
        if verbose:
            print(f"[ANIM MAP] JSON 파싱 실패: {p} err={e}", flush=True)
        return

    if verbose:
        print(
            f"[ANIM MAP] 이벤트={seq} -> JSON 준비완료: {p} "
            f"(runner={runner}, rule={rule_name or '-'}, lot={payload.get('lot_id','')}, port={payload.get('port_id','')}, "
            f"from={payload.get('from_port_id','')}, to={payload.get('to_port_id','')})",
            flush=True,
        )
        print("[ANIM MAP] TODO: _execute_mapped_sequence_stub에서 실제 SequenceRunner 실행 연결", flush=True)


def build_control_window(ext: Any) -> None:
    """TBS 제어창을 만들고 ext에 위젯/모델 참조를 저장."""
    ext._usd_anim_start_frame = ui.SimpleIntModel(200)
    ext._usd_anim_end_frame = ui.SimpleIntModel(300)
    ext._usd_anim_loop = ui.SimpleBoolModel(False)
    ext._usd_anim_auto_range_text = ui.SimpleStringModel("AUTO RANGE: (미확인)")
    ext._xml_from_port_model = ui.SimpleIntModel(1)
    ext._xml_to_port_model = ui.SimpleIntModel(6)
    ext._xml_port_id_model = ui.SimpleIntModel(1)
    ext._last_generated_xml = ""
    ext._priority_prefix_model = ui.SimpleStringModel(DEFAULT_PRIORITY_NAME_PREFIX)
    ext._sim_lot_count_model = ui.SimpleIntModel(6)
    ext._sim_metro_min_model = ui.SimpleFloatModel(10.0)
    ext._sim_metro_max_model = ui.SimpleFloatModel(300.0)
    ext._sim_speed_model = ui.SimpleFloatModel(1.0)
    ext._sim_log_interval_model = ui.SimpleFloatModel(5.0)
    ext._sim_oht_bp1_min_model = ui.SimpleFloatModel(15.0)
    ext._sim_oht_bp1_max_model = ui.SimpleFloatModel(120.0)
    ext._sim_bp1_bp_min_model = ui.SimpleFloatModel(3.0)
    ext._sim_bp1_bp_max_model = ui.SimpleFloatModel(3.0)
    ext._sim_bp_ep_min_model = ui.SimpleFloatModel(2.0)
    ext._sim_bp_ep_max_model = ui.SimpleFloatModel(2.0)
    ext._sim_ep_oht_min_model = ui.SimpleFloatModel(1.0)
    ext._sim_ep_oht_max_model = ui.SimpleFloatModel(1.0)
    ext._sim_ep_count_combo = None
    ext._sim_init_bp1_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp2_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp3_model = ui.SimpleBoolModel(False)
    ext._sim_init_bp4_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep1_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep2_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep3_model = ui.SimpleBoolModel(False)
    ext._sim_init_ep3_row = None
    ext._sim_log_text = ui.SimpleStringModel("[SIM] 대기 중")
    ext._sim_history_text = ui.SimpleStringModel("[SIM] 대기 중")
    ext._sim_progress_text = ui.SimpleStringModel("[진행현황] 없음")
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}
    ext._sim_engine = None
    ext._sim_update_sub = None
    ext._sim_thread = None
    ext._sim_thread_stop = None
    ext._sim_log_queue = None
    ext._sim_log_ui_sub = None
    ext._sim_log_view_combo = None
    ext._sim_progress_frame = None
    ext._sim_history_frame = None
    ext._sim_progress_label = None
    ext._sim_history_label = None

    ext._control_window = ui.Window("TBS 제어창", width=460, height=640)
    with ext._control_window.frame:
        with ui.VStack(spacing=0):
            ui.Label("USD 파일 애니메이션 (타임라인)", height=0)
            with ui.HStack(spacing=8, height=28):
                ui.Label("범위", width=50, height=28)
                ext._usd_anim_mode_combo = ui.ComboBox(0, "수동", "자동")
                ext._usd_anim_mode_combo.model.add_item_changed_fn(lambda m, *a: on_usd_anim_mode_changed(ext))
                ui.Label("", width=0)
            ext._usd_anim_manual_frame_row = ui.HStack(spacing=8, height=30)
            with ext._usd_anim_manual_frame_row:
                ui.Label("시작 프레임", width=70, height=30)
                ui.IntField(model=ext._usd_anim_start_frame, width=60, height=30)
                ui.Label("끝 프레임", width=70, height=30)
                ui.IntField(model=ext._usd_anim_end_frame, width=60, height=30)
            ext._usd_anim_auto_range_row = ui.HStack(spacing=8, height=22)
            with ext._usd_anim_auto_range_row:
                ui.Label("AUTO", width=50, height=22)
                ui.Label("", model=ext._usd_anim_auto_range_text, height=22)
            ext._usd_anim_manual_frame_row.visible = True
            ext._usd_anim_auto_range_row.visible = False
            with ui.HStack(spacing=8, height=20):
                ui.CheckBox(model=ext._usd_anim_loop)
                ui.Label("루프", height=0)
            ui.Button("USD 파일 애니메이션 재생", height=28, clicked_fn=lambda: on_play_usd_animation(ext))
            ui.Button("USD 애니메이션 정지", height=24, clicked_fn=usd_animation_control.stop_usd_animation)
            ui.Spacer(height=6)
            ui.Button("가상 시그널 재생 (JSON 샘플)", height=28, clicked_fn=lambda: on_play_generator_sample(ext))
            ui.Spacer(height=6)
            ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
            ui.Spacer(height=6)
            with ui.Frame(style={"background_color": 0xFF23262B}):
                with ui.VStack(padding=8, spacing=6):
                    with ui.HStack(spacing=8, height=28):
                        ui.Label("XML 제너레이터 생성기", width=150, height=28, style={"color": 0xFFDDDDDD})
                        ext._xml_seq_combo = ui.ComboBox(
                            0,
                            xml_generator.SEQ_READYTOLOAD,
                            xml_generator.SEQ_ARRIVED,
                            xml_generator.SEQ_MOVE_TRANSFERING,
                            xml_generator.SEQ_MOVE,
                            xml_generator.SEQ_READYTOUNLOAD,
                            xml_generator.SEQ_REMOVED,
                        )
                        ext._xml_seq_combo.model.add_item_changed_fn(lambda m, *a: on_xml_seq_changed(ext))
                        ui.Button("OK", width=60, height=28, clicked_fn=lambda: on_xml_ok_clicked(ext))
                    ext._xml_ab_inputs_frame = ui.HStack(spacing=8, height=28)
                    with ext._xml_ab_inputs_frame:
                        ui.Label("FROM_PORT_ID", width=110, height=28)
                        ui.IntField(model=ext._xml_from_port_model, width=60, height=28)
                        ui.Label("TO_PORT_ID", width=90, height=28)
                        ui.IntField(model=ext._xml_to_port_model, width=60, height=28)
                    ext._xml_ab_inputs_frame.visible = True

                    ext._xml_port_inputs_frame = ui.HStack(spacing=8, height=28)
                    with ext._xml_port_inputs_frame:
                        ui.Label("PORT_ID", width=110, height=28)
                        ui.IntField(model=ext._xml_port_id_model, width=60, height=28)
                    ext._xml_port_inputs_frame.visible = False
                    # 콤보 초기 선택값 기준으로 입력 필드 표시 상태 동기화
                    on_xml_seq_changed(ext)
                    ui.Button("제너레이터 실행(역파싱)", height=28, clicked_fn=lambda: on_xml_run_clicked(ext))
            ui.Spacer(height=6)
            ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
            ui.Spacer(height=6)
            with ui.Frame(style={"background_color": 0xFF1E2530}):
                with ui.VStack(padding=8, spacing=6):
                    ui.Label("시뮬레이션 (simpy)", height=24, style={"color": 0xFFDDDDDD})
                    with ui.HStack(spacing=8, height=28):
                        ui.Label("LOT 수", width=80)
                        ui.IntField(model=ext._sim_lot_count_model, width=80)
                        ui.Label("EP 개수", width=55)
                        ext._sim_ep_count_combo = ui.ComboBox(0, "2", "3")
                        ext._sim_ep_count_combo.model.add_item_changed_fn(lambda m, *a: on_sim_ep_count_changed(ext))
                        ui.Label("metro(s) MIN", width=90)
                        ui.FloatField(model=ext._sim_metro_min_model, width=80)
                        ui.Label("MAX", width=35)
                        ui.FloatField(model=ext._sim_metro_max_model, width=80)
                    ui.Label("초기 LOT 적재 포트 (체크 시 시작 시점에 FULL)", height=20)
                    with ui.HStack(spacing=8, height=26):
                        ui.Label("BP1", width=30); ui.CheckBox(model=ext._sim_init_bp1_model)
                        ui.Label("BP2", width=30); ui.CheckBox(model=ext._sim_init_bp2_model)
                        ui.Label("BP3", width=30); ui.CheckBox(model=ext._sim_init_bp3_model)
                        ui.Label("BP4", width=30); ui.CheckBox(model=ext._sim_init_bp4_model)
                    with ui.HStack(spacing=8, height=26):
                        ui.Label("EP1", width=30); ui.CheckBox(model=ext._sim_init_ep1_model)
                        ui.Label("EP2", width=30); ui.CheckBox(model=ext._sim_init_ep2_model)
                        ext._sim_init_ep3_row = ui.HStack(spacing=8, height=26)
                        with ext._sim_init_ep3_row:
                            ui.Label("EP3", width=30); ui.CheckBox(model=ext._sim_init_ep3_model)
                    on_sim_ep_count_changed(ext)
                    with ui.HStack(spacing=8, height=28):
                        ui.Label("OHT->BP1", width=80)
                        ui.FloatField(model=ext._sim_oht_bp1_min_model, width=70)
                        ui.Label("~", width=10)
                        ui.FloatField(model=ext._sim_oht_bp1_max_model, width=70)
                        ui.Label("BP1->BP", width=60)
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
                        ui.Button("시작", width=80, clicked_fn=lambda: on_sim_start_clicked(ext))
                        ui.Button("정지", width=80, clicked_fn=lambda: on_sim_stop_clicked(ext))
                        ui.Button("리셋", width=80, clicked_fn=lambda: on_sim_reset_clicked(ext))
                    with ui.HStack(spacing=8, height=24):
                        ui.Label("표시모드", width=60)
                        ext._sim_log_view_combo = ui.ComboBox(0, "둘다", "진행현황", "이력로그")
                        ext._sim_log_view_combo.model.add_item_changed_fn(lambda m, *a: on_sim_log_view_changed(ext))
                        ui.Button("진행현황 복사", width=100, clicked_fn=lambda: on_copy_sim_progress(ext))
                    ext._sim_progress_frame = ui.ScrollingFrame(height=90)
                    with ext._sim_progress_frame:
                        ext._sim_progress_label = ui.Label("", word_wrap=True, width=0, height=0, style={"color": 0xFFFFFFFF})
                        ext._sim_progress_label.text = ext._sim_progress_text.as_string
                    ext._sim_history_frame = ui.ScrollingFrame(height=140)
                    with ext._sim_history_frame:
                        ext._sim_history_label = ui.Label("", word_wrap=True, width=0, height=0, style={"color": 0xFFFFFFFF})
                        ext._sim_history_label.text = ext._sim_history_text.as_string
                    on_sim_log_view_changed(ext)
            ui.Spacer(height=6)
            ui.Rectangle(height=2, style={"background_color": 0xFF3A3A3A})
            ui.Spacer(height=8)
            ui.Label("우선 표시 이름 규칙 (접두사, 비우면 순서대로 표시)", height=0)
            ui.StringField(model=ext._priority_prefix_model, height=22)
            ui.Spacer(height=4)
            ui.Label("로드된 USD 내 장비 prim (드롭다운)", height=0)
            ui.Button("목록 새로고침", height=28, clicked_fn=lambda: on_refresh_prim_list(ext))
            ui.Spacer(height=4)
            with ui.ScrollingFrame(style={"ScrollingFrame": {"padding": 0, "margin": 0}}):
                ext._object_list_frame = ui.VStack(height=0, alignment=ui.Alignment.LEFT_TOP)
    refresh_object_list(ext)


def on_usd_anim_mode_changed(ext: Any) -> None:
    try:
        idx = ext._usd_anim_mode_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    is_auto = idx == 1
    ext._usd_anim_manual_frame_row.visible = not is_auto
    ext._usd_anim_auto_range_row.visible = is_auto
    if is_auto:
        rng = usd_animation_control.resolve_saved_animation_frame_range()
        if rng:
            ext._usd_anim_auto_range_text.set_value(f"AUTO RANGE: {rng[0]} ~ {rng[1]}")
        else:
            ext._usd_anim_auto_range_text.set_value("AUTO RANGE: (감지 실패)")


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
        print(f"[morph.tbs_control_1][xml_generator] XML 생성 실패: {e}", flush=True)


def on_xml_run_clicked(ext: Any) -> None:
    xml_text = (ext._last_generated_xml or "").strip()
    if not xml_text:
        print("[morph.tbs_control_1][xml_generator] 저장된 XML이 없습니다. 먼저 OK로 XML을 생성하세요.", flush=True)
        return
    parsed = xml_generator.parse_xml_string(xml_text)
    if not parsed:
        print("[morph.tbs_control_1][xml_generator] XML 역파싱 실패.", flush=True)
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


def _append_sim_log(ext: Any, line: str) -> None:
    msg = (line or "").strip()
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


def _enqueue_sim_log(ext: Any, line: str) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        q.put_nowait(("log", (line or "").strip()))
    except Exception:
        pass


def _enqueue_sim_progress(ext: Any, payload: Dict[str, str]) -> None:
    q = getattr(ext, "_sim_log_queue", None)
    if q is None:
        return
    try:
        q.put_nowait(("progress", dict(payload or {})))
    except Exception:
        pass


def _drain_sim_log_queue(ext: Any) -> None:
    try:
        q = getattr(ext, "_sim_log_queue", None)
        if q is None:
            return
        try:
            view_idx = ext._sim_log_view_combo.model.get_item_value_model().as_int
        except Exception:
            view_idx = 0
        count = 0
        while count < 200:
            try:
                item = q.get_nowait()
            except Exception:
                break
            kind, payload = item if isinstance(item, tuple) and len(item) == 2 else ("log", item)
            if kind == "progress":
                _update_sim_progress(ext, payload if isinstance(payload, dict) else {})
            else:
                line = payload if isinstance(payload, str) else str(payload)
                # 진행현황 전용 모드(1)에서는 이력 로그 누적을 생략
                if line and view_idx != 1:
                    _append_sim_log(ext, line)
            count += 1
    except Exception as e:
        # UI 드레인 예외가 발생해도 구독이 끊기지 않도록 보호
        print(f"[SIM UI] 로그 드레인 예외: {e}", flush=True)


def _update_sim_progress(ext: Any, payload: Dict[str, str]) -> None:
    label = str(payload.get("label", "")).strip()
    if not label:
        return
    status = str(payload.get("status", "RUNNING"))
    percent = str(payload.get("percent", "0"))
    elapsed = str(payload.get("elapsed", "0.0"))
    total = str(payload.get("total", "0.0"))
    sim_time = str(payload.get("sim_time", "0.00"))
    detail = str(payload.get("detail", ""))
    history = getattr(ext, "_sim_progress_history", None)
    start_times = getattr(ext, "_sim_progress_start_times", None)
    if history is None or start_times is None:
        return

    try:
        now_t = float(sim_time)
    except Exception:
        now_t = 0.0
    try:
        elapsed_t = float(elapsed)
    except Exception:
        elapsed_t = 0.0

    if label not in start_times:
        start_times[label] = max(0.0, now_t - elapsed_t)
    start_t = float(start_times.get(label, max(0.0, now_t - elapsed_t)))
    end_t = now_t
    dur_t = max(0.0, end_t - start_t)

    line = (
        f"[t={start_t:.2f}~{end_t:.2f} | {dur_t:.1f}s] "
        f"{label} | {percent}% ({elapsed}/{total}s) | {status} | {detail}"
    )

    # 최신이 위로 쌓이도록 관리
    if status == "DONE":
        history.insert(0, line + " | 완료")
        start_times.pop(label, None)
        current_lines: List[str] = []
    else:
        current_lines = [line]

    # 현재 진행중 1줄 + 직전 완료 내역(최신순)
    text_lines = current_lines + history[:80]
    text = "\n".join(text_lines) if text_lines else "[진행현황] 없음"
    ext._sim_progress_text.set_value(text)
    if getattr(ext, "_sim_progress_label", None) is not None:
        ext._sim_progress_label.text = text


def _on_sim_event(ext: Any, payload: Dict[str, str]) -> None:
    seq_raw = (payload.get("seq") or "").strip()
    if not seq_raw:
        return
    seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)
    lot_id = payload.get("lot_id", "")
    sim_time = payload.get("sim_time", "")

    def _parse_port_num(port_text: str, default_value: int = 1) -> int:
        txt = (port_text or "").strip().upper()
        for prefix in ("BP", "EP", "PORT_"):
            txt = txt.replace(prefix, "")
        try:
            return int(txt)
        except Exception:
            return default_value

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
    txt = (port_text or "").strip().upper()
    for prefix in ("BP", "EP", "PORT_"):
        txt = txt.replace(prefix, "")
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


def handle_sim_event_for_animation(payload: Dict[str, str], verbose: bool = True) -> None:
    """
    시뮬레이션 이벤트 -> 애니메이션 실행 훅.
    현재는 분기별 로그만 출력하고, 추후 분기 내부에 실제 애니메이션 함수를 연결한다.
    """
    seq_raw = (payload.get("seq") or "").strip()
    if not seq_raw:
        return
    seq = SIM_SEQ_ALIAS.get(seq_raw, seq_raw)
    sim_time = payload.get("sim_time", "")
    lot_id = payload.get("lot_id", "")
    from_port_txt = str(payload.get("from_port_id", ""))
    to_port_txt = str(payload.get("to_port_id", ""))
    port_txt = str(payload.get("port_id", ""))
    from_kind = _port_kind(from_port_txt)
    to_kind = _port_kind(to_port_txt)
    port_kind = _port_kind(port_txt)

    if verbose:
        print(
            f"[ANIM HOOK t={sim_time}] 이벤트={seq_raw}->{seq} lot={lot_id} "
            f"port={port_txt}({port_kind}) from={from_port_txt}({from_kind}) to={to_port_txt}({to_kind})",
            flush=True,
        )

    # 이벤트 매핑(JSON) 조회 및 실행 훅
    mapped_json, mapped_meta, matched_rule = _resolve_event_animation_entry(seq, payload)
    if mapped_json:
        _execute_mapped_sequence_stub(seq, payload, mapped_json, mapped_meta, matched_rule, verbose)
    elif verbose:
        print(
            f"[ANIM MAP] 이벤트={seq} 매핑 없음 "
            f"(config/event_animation_rules.json / event_animation_map.json 확인)",
            flush=True,
        )

    try:
        if seq in xml_generator.FROM_TO_SEQS:
            from_port = _parse_port_num(from_port_txt, 1)
            to_port = _parse_port_num(to_port_txt, 1)
            xml_text = xml_generator.build_xml_string(seq, from_port_id=from_port, to_port_id=to_port)
        else:
            port = _parse_port_num(port_txt, 1)
            xml_text = xml_generator.build_xml_string(seq, port_id=port)
        parsed = xml_generator.parse_xml_string(xml_text) or {}
        action_desc = parsed.get("action_desc", "")
        if action_desc and verbose:
            if port_txt:
                action_desc += f" | 대상포트={port_txt}({port_kind})"
            if from_port_txt or to_port_txt:
                action_desc += f" | 이동경로={from_port_txt}({from_kind})->{to_port_txt}({to_kind})"
            print(f"[ANIM HOOK ACTION] {action_desc}", flush=True)
    except Exception as e:
        if verbose:
            print(f"[ANIM HOOK] XML 생성/역파싱 실패: seq={seq}, err={e}", flush=True)
        return

    # 추후 실제 애니메이션 분기 지점
    if seq == xml_generator.SEQ_READYTOLOAD:
        if verbose:
            print(f"[ANIM PLAN] READY_TO_LOAD 대기 상태 애니메이션 | port={port_txt}({port_kind})", flush=True)
    elif seq == xml_generator.SEQ_ARRIVED:
        if verbose:
            print(f"[ANIM PLAN] ARRIVED 안착 애니메이션 | port={port_txt}({port_kind}) lot={lot_id}", flush=True)
    elif seq == xml_generator.SEQ_MOVE_TRANSFERING:
        if verbose:
            print(f"[ANIM PLAN] MOVE_TRANSFERING 이송 애니메이션 | from={from_port_txt}({from_kind}) to={to_port_txt}({to_kind}) lot={lot_id}", flush=True)
    elif seq == xml_generator.SEQ_MOVE:
        if verbose:
            print(f"[ANIM PLAN] MOVE 이동 애니메이션 | from={from_port_txt}({from_kind}) to={to_port_txt}({to_kind}) lot={lot_id}", flush=True)
    elif seq == xml_generator.SEQ_READYTOUNLOAD:
        if verbose:
            print(f"[ANIM PLAN] READY_TO_UNLOAD 회수 준비 애니메이션 | port={port_txt}({port_kind}) lot={lot_id}", flush=True)
    elif seq == xml_generator.SEQ_REMOVED:
        if verbose:
            print(f"[ANIM PLAN] REMOVED 회수 완료 애니메이션 | port={port_txt}({port_kind}) lot={lot_id}", flush=True)
    else:
        if verbose:
            print(f"[ANIM PLAN] 미분류 이벤트 | seq={seq} payload={payload}", flush=True)


def _is_progress_only_mode(ext: Any) -> bool:
    try:
        return ext._sim_log_view_combo.model.get_item_value_model().as_int == 1
    except Exception:
        return False


def _detach_sim_update(ext: Any) -> None:
    sub = getattr(ext, "_sim_update_sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:
            pass
        ext._sim_update_sub = None

    stop_evt = getattr(ext, "_sim_thread_stop", None)
    th = getattr(ext, "_sim_thread", None)
    if stop_evt is not None:
        try:
            stop_evt.set()
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


def on_sim_start_clicked(ext: Any) -> None:
    try:
        ep_count_idx = ext._sim_ep_count_combo.model.get_item_value_model().as_int
    except Exception:
        ep_count_idx = 0
    ep_count = 2 if ep_count_idx == 0 else 3

    initial_full_ports: List[str] = []
    if ext._sim_init_bp1_model.get_value_as_bool():
        initial_full_ports.append("BP1")
    if ext._sim_init_bp2_model.get_value_as_bool():
        initial_full_ports.append("BP2")
    if ext._sim_init_bp3_model.get_value_as_bool():
        initial_full_ports.append("BP3")
    if ext._sim_init_bp4_model.get_value_as_bool():
        initial_full_ports.append("BP4")
    if ext._sim_init_ep1_model.get_value_as_bool():
        initial_full_ports.append("EP1")
    if ext._sim_init_ep2_model.get_value_as_bool():
        initial_full_ports.append("EP2")
    if ep_count >= 3 and ext._sim_init_ep3_model.get_value_as_bool():
        initial_full_ports.append("EP3")

    on_sim_stop_clicked(ext)
    lot_count = max(1, ext._sim_lot_count_model.get_value_as_int())
    min_required = max(1, len(initial_full_ports))
    lot_count = max(lot_count, min_required)
    metro_min = max(0.1, ext._sim_metro_min_model.get_value_as_float())
    metro_max = max(0.1, ext._sim_metro_max_model.get_value_as_float())
    if metro_min > metro_max:
        metro_min, metro_max = metro_max, metro_min
    timing = SimulationTimingConfig(
        oht_to_bp1_min=max(0.1, ext._sim_oht_bp1_min_model.get_value_as_float()),
        oht_to_bp1_max=max(0.1, ext._sim_oht_bp1_max_model.get_value_as_float()),
        bp1_to_bp_min=max(0.1, ext._sim_bp1_bp_min_model.get_value_as_float()),
        bp1_to_bp_max=max(0.1, ext._sim_bp1_bp_max_model.get_value_as_float()),
        bp_to_ep_min=max(0.1, ext._sim_bp_ep_min_model.get_value_as_float()),
        bp_to_ep_max=max(0.1, ext._sim_bp_ep_max_model.get_value_as_float()),
        ep_to_oht_min=max(0.1, ext._sim_ep_oht_min_model.get_value_as_float()),
        ep_to_oht_max=max(0.1, ext._sim_ep_oht_max_model.get_value_as_float()),
    )
    log_interval = max(0.0, ext._sim_log_interval_model.get_value_as_float())
    log_cfg = SimulationLogConfig(
        progress_interval_sec=log_interval,
        input_status_interval_sec=log_interval,
    )
    init_cfg = SimulationInitConfig(ep_count=ep_count, initial_full_ports=initial_full_ports)
    lots: List[Lot] = []
    for i in range(lot_count):
        lot_id = f"LOT_{i+1:03d}"
        foup_id = f"FOUP_{i+1:03d}"
        metro = random.uniform(metro_min, metro_max)
        lots.append(Lot(lot_id=lot_id, foup_id=foup_id, sequence=i + 1, metro_time=metro))

    ext._sim_history_text.set_value("[SIM] 초기화")
    ext._sim_progress_text.set_value("[진행현황] 초기화 (시뮬레이션 시작 대기)")
    if getattr(ext, "_sim_history_label", None) is not None:
        ext._sim_history_label.text = "[SIM] 초기화"
    if getattr(ext, "_sim_progress_label", None) is not None:
        ext._sim_progress_label.text = "[진행현황] 초기화 (시뮬레이션 시작 대기)"
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}
    ext._sim_log_queue = queue.SimpleQueue()
    _enqueue_sim_log(ext, "[SIM UI] 실시간 로그 큐 초기화")
    engine = TBSSimulationEngine(
        lots=lots,
        timing=timing,
        log_config=log_cfg,
        init_config=init_cfg,
        # 시뮬레이션 스레드에서 발생하는 로그/이벤트는 큐에 넣고 UI 스레드에서 표시
        on_log=lambda line: _enqueue_sim_log(ext, line),
        on_event=lambda payload: handle_sim_event_for_animation(payload, verbose=(not _is_progress_only_mode(ext))),
        on_progress=lambda payload: _enqueue_sim_progress(ext, payload),
        print_to_console=(not _is_progress_only_mode(ext)),
    )
    ext._sim_engine = engine
    if not engine.start():
        _append_sim_log(ext, "[SIM] 시작 실패")
        return

    tick_state = {"count": 0}
    stop_evt = threading.Event()
    ext._sim_thread_stop = stop_evt
    speed_value = max(0.1, ext._sim_speed_model.get_value_as_float())
    _append_sim_log(ext, f"[SIM] tick thread 준비 (speed={speed_value:.2f}x)")
    try:
        ext._sim_log_ui_sub = app.get_app().get_update_event_stream().create_subscription_to_pop(
            lambda e: _drain_sim_log_queue(ext),
            name="morph.tbs_control_1:sim_log_ui_drain",
        )
    except Exception as e:
        _append_sim_log(ext, f"[SIM UI] 로그 큐 드레인 구독 실패: {e}")

    def _tick_loop():
        try:
            print("[SIM] tick thread 시작", flush=True)
            last = time.perf_counter()
            while not stop_evt.is_set():
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
                    break
                time.sleep(0.02)
        except Exception as err:
            print(f"[SIM] tick thread 예외: {err}", flush=True)

    th = threading.Thread(target=_tick_loop, name="morph.tbs_control_1.sim_tick", daemon=True)
    ext._sim_thread = th
    th.start()


def on_sim_stop_clicked(ext: Any) -> None:
    sim = getattr(ext, "_sim_engine", None)
    if sim is not None:
        try:
            sim.stop()
        except Exception:
            pass
    _detach_sim_update(ext)


def on_sim_reset_clicked(ext: Any) -> None:
    on_sim_stop_clicked(ext)
    ext._sim_engine = None
    if getattr(ext, "_sim_history_text", None):
        ext._sim_history_text.set_value("[SIM] 리셋 완료")
    if getattr(ext, "_sim_history_label", None) is not None:
        ext._sim_history_label.text = "[SIM] 리셋 완료"
    if getattr(ext, "_sim_progress_text", None):
        ext._sim_progress_text.set_value("[진행현황] 없음")
    if getattr(ext, "_sim_progress_label", None) is not None:
        ext._sim_progress_label.text = "[진행현황] 없음"
    ext._sim_progress_rows = {}
    ext._sim_progress_history = []
    ext._sim_progress_start_times = {}


def on_sim_log_view_changed(ext: Any) -> None:
    try:
        idx = ext._sim_log_view_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    # 0:둘다, 1:진행현황, 2:이력로그
    if getattr(ext, "_sim_progress_frame", None) is not None:
        ext._sim_progress_frame.visible = idx in (0, 1)
    if getattr(ext, "_sim_history_frame", None) is not None:
        ext._sim_history_frame.visible = idx in (0, 2)
    sim = getattr(ext, "_sim_engine", None)
    if sim is not None and hasattr(sim, "set_console_logging_enabled"):
        # 진행현황 전용 모드에서는 콘솔/이력 로그 최소화
        sim.set_console_logging_enabled(idx != 1)


def on_copy_sim_progress(ext: Any) -> None:
    text = ""
    if getattr(ext, "_sim_progress_label", None) is not None:
        text = ext._sim_progress_label.text or ""
    if not text.strip() and getattr(ext, "_sim_progress_text", None):
        text = ext._sim_progress_text.as_string or ""
    if not text.strip():
        _append_sim_log(ext, "[SIM UI] 복사할 진행현황이 없습니다.")
        return
    try:
        import omni.kit.clipboard as cb  # type: ignore
        if hasattr(cb, "copy"):
            cb.copy(text)
        elif hasattr(cb, "set_text"):
            cb.set_text(text)
        else:
            raise RuntimeError("clipboard api not found")
        _append_sim_log(ext, "[SIM UI] 진행현황 복사 완료")
    except Exception:
        print("[SIM UI] 클립보드 미지원: 진행현황을 콘솔에 출력합니다.", flush=True)
        print(text, flush=True)
        _append_sim_log(ext, "[SIM UI] 클립보드 미지원으로 콘솔 출력")


def on_sim_ep_count_changed(ext: Any) -> None:
    try:
        idx = ext._sim_ep_count_combo.model.get_item_value_model().as_int
    except Exception:
        idx = 0
    is_ep3 = idx == 1
    if getattr(ext, "_sim_init_ep3_row", None) is not None:
        ext._sim_init_ep3_row.visible = is_ep3
    if not is_ep3 and getattr(ext, "_sim_init_ep3_model", None) is not None:
        ext._sim_init_ep3_model.set_value(False)


def on_play_usd_animation(ext: Any) -> None:
    loop = ext._usd_anim_loop.get_value_as_bool()
    try:
        mode = ext._usd_anim_mode_combo.model.get_item_value_model().as_int
    except Exception:
        mode = 0
    if mode == 1:
        rng = usd_animation_control.resolve_saved_animation_frame_range()
        if not rng:
            print("[USD ANIM] 자동 범위 감지 실패.", flush=True)
            return
        start, end = int(rng[0]), int(rng[1])
        ext._usd_anim_auto_range_text.set_value(f"AUTO RANGE: {start} ~ {end}")
    else:
        start = ext._usd_anim_start_frame.get_value_as_int()
        end = ext._usd_anim_end_frame.get_value_as_int()
    usd_animation_control.play_usd_animation(
        start_frame=start, end_frame=end, loop=loop,
        on_completed=(lambda: print(f"[USD ANIM] 완료: {start}~{end}", flush=True)) if not loop else None,
    )


def on_play_generator_sample(ext: Any) -> None:
    parsed = parse_signal(SAMPLE_GENERATOR_JSON, "json")
    if parsed:
        run_generator_from_parsed(ext, parsed)


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
        if getattr(ext, "_load_status_label", None):
            ext._load_status_label.text = "스테이지가 없습니다. USD를 먼저 로드하세요."
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
