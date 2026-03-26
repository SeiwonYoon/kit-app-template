## Slide 1) Architecture overview (전문가용)

### Runtime topology
- **UI thread (Omni.UI)**: control window 렌더링/입력 처리, 큐 드레인, 애니 실행 트리거, 엑셀 export
- **Simulation thread (simpy tick)**: `TBSSimulationEngine` 실행, 이벤트/로그/progress를 thread-safe queue로 전달

### Core modules (핵심 책임)
- `simulation_engine.py`
  - simpy 기반 공정 상태머신(포트 점유/선택 정책/시간 모델)
  - 이벤트 발행: `seq/from/to/port/lot + ports_occupancy + sim_time`
  - 게이트(on_gate): “각 공정 확인” 동기 블로킹 포인트
- `control_window.py`
  - UI 모델/옵션 → `SimulationTimingConfig/InitConfig/LogConfig` 구성
  - 큐 드레인: progress/history/anim_event/gate/action 처리
  - 애니 연결: **event → XML build → XML parse → rules/map resolve → JSON validate → SequenceRunner.run**
  - 종료 처리: 엑셀(xlsx) 3시트 자동 저장 + 애니 실행 레코드(열 분리)
- `xml_generator.py`
  - EAPEIS 포트 이벤트 XML 생성/역파싱(정식 seq/port fields 확보)
- `sequence_engine.py` / `sequence_editor.py`
  - JSON 시퀀스 실행(MOVE/ROTATE/DELAY/USD_TIMELINE), 랜덤 범위 샘플링
  - “현재 위치부터 시작” 스냅샷 저장/복원
- `event_animation_rules.json` / `event_animation_map.json`
  - 상태 기반(ports_occupancy 포함) 매핑 룰, fallback 맵

---

## Slide 2) Execution flow (전문가용: time-ordered)

### Simulation (serial flow, 1 LOT at a time)
`_run_serial_flow()` loop:
- Input gating: `_can_load_to_bp1()` 만족 시
  - `_load_lot_to_bp1(lot)` (OHT→BP1) → `_move_bp1_to_buffer()` (BP1→oldest empty BP)
- Dispatch gating:
  - `_find_oldest_bp()` + `_find_empty_ep()` 만족 시
  - `_move_bp_to_ep(bp, ep, lot)` → `_process_ep_lot(ep, lot)` → `_ready_to_unload(ep)`

### Event→XML→Animation binding (single source of truth: XML)
Per event payload:
1) **Canonicalize**: `SIM_SEQ_ALIAS`로 seq 정규화
2) **XML build**: `xml_generator.build_xml_string(seq, from/to/port_id...)`
3) **XML parse**: `xml_generator.parse_xml_string(xml)` → `sequence_name/from_port_id/to_port_id/port_id`
4) **Standardize mapping payload**: XML parse 결과로 payload를 덮어써 rules input을 “XML 기준”으로 고정
5) **Resolve**:
   - `event_animation_rules.json` 우선(when: sequence/from/to/port + ports_occupancy)
   - 없으면 `event_animation_map.json` fallback
6) **Validate+Run**:
   - JSON 존재/파싱 검증 후 `SequenceRunner.run(steps)`
   - 애니 실행 레코드: `sim_time`, `event`, `file/path`, `est_sec`, `wall_sec`, `status` 등을 누적 → xlsx 열 분리 저장

### Observability & export
- UI display mode: 진행현황 / 이력로그 / 애니메이션실행이력 / 둘다
- 종료 시 `_export_sim_logs_to_xlsx()`:
  - Sheet1=진행현황, Sheet2=이력로그, Sheet3=애니메이션실행이력(열 분리, 최신순)
