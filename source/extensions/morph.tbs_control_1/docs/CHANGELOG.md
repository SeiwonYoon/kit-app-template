# Changelog

## Unreleased

- **EP 타임라인 시계 정합** (`simulation_engine.py`): `timeline_only` progress의 `sim_time`을 내부 `virtual_now`가 아니라 SimPy **`env.now`**로 emit. 진행현황 `t(sim)`·포트 아래 막대 축이 같은 시계를 쓰며, 분할/단일 모두에서 “막대만 더 빨리 가는” 현상을 막는다.
- **Kit EP 막대(포트 아래)** (`control_window.py`):
  - 단일 모니터: 동일 시뮼 시각·레이아웃이면 VStack 전체 destroy/rebuild를 생략해 재생 시 깜빡임 완화.
  - 뷰포트 분할(2~4): `_ep_occ_timeline_layout_dims`로 막대·이름·우측 초·패딩·행 간격 축소, `ep_timeline_host`에 **가로 스크롤**(환경에 따라 AS_NEEDED/AUTO/ALWAYS_ON 순)로 열 폭보다 넓은 행이 잘리지 않게 함.
- **분할 시 이력 로그 라우팅** (`control_window.py`): 멀티 채널에서 `[화면N]` 접두는 **`_format_history_line` 적용 전 원문**에서만 파싱해 해당 `history_label`에만 붙임. 포맷 단계에서 줄 앞에 붙는 이모지 때문에 `^\[화면…` 매칭이 깨져 전 로그가 화면1에만 쌓이던 문제를 해소.

## 0.1.0
- TBS Control과 동일 기능. 기능별 모듈 분리 (usd_loader_utils, prim_utils, load_window, control_window, selection_overlay, sequence_editor 등).
- extension.py: 기본 UI 구성 및 모듈 함수 호출만 포함, 상세 주석.
