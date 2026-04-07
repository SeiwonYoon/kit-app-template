sim_sequences 템플릿 폴더

- 이 폴더의 JSON은 `control_window.EVENT_JSON_CASE_MAP`(최우선) 또는 `config/event_animation_rules.json` / `event_animation_map.json`에서 참조됩니다.
- 파일명 규칙(시뮬 이벤트와 대응):
  - `arrived_inout.json` — OHT→IN/OUT 안착
  - `arrived_ep1.json` … `arrived_ep3.json` — OHT→EP 직접 투입
  - `move_inout_bp1.json` … `move_inout_bp4.json` — IN/OUT→버퍼 BPn
  - `move_bp{1..4}_ep{1..3}.json` — 버퍼→EP (예: `move_bp1_ep1.json`)
  - `removed_ep1.json` … — EP에서 회수 완료 연출
- 시퀀스 편집기에서 저장한 JSON(list 루트)을 같은 파일명으로 덮어쓰면 시뮬레이션 이벤트 시 자동 실행됩니다.
- 비어 있는 템플릿은 모두 `[]` 입니다.
