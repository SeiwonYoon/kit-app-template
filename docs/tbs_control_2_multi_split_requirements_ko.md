# TBS Control 2 — 멀티 분할 화면·독립 시뮬 요구사항 및 구현 가이드

> **용도**: 코드 원복 후 재구현 시 이 문서만 따라 동일 요구를 만족한다.  
> **대상 확장**: `morph.tbs_control_2`  
> **최종 갱신**: 2026-06-18

---

## 1-d. 2026-06-18 — 원복 후 구조적 재작업 SSOT

**코드 원복 완료** (1화면 OK · 2화면 미해결). 다음 세션부터 아래 문서를 **먼저** 따른다.

→ **[tbs_control_2_playback_structural_redesign_ko.md](tbs_control_2_playback_structural_redesign_ko.md)**

포함 내용: 2화면 진행률 실시간 반영, proc/json 타이밍, JSON 스텝 간섭, FOUP 실시간 — **증상 패치 금지**, Phase 0–4 순서, 불변 조건 B1–B7.

이하 §1-c(PlaybackChannel 전면 교체) 등 **2026-06-17 시도**는 원복됨 — 참고만 하고 재적용 시 redesign 문서 Phase 순서를 따른다.

---

상세: [`tbs_control_2_playback_debug_history_ko.md` §16](tbs_control_2_playback_debug_history_ko.md)

- 신규 ``playback_channel.py`` — ``PlaybackChannel`` + ``PlaybackChannelRuntime``
- 진행률 UI = 화면별 Kit 구독 (emit·JSON 큐와 **완전 분리**)
- JSON = ``channel.enqueue_main_job``

---

Phase A(tick·세션) 이후에도 2화면 JSON 끊김이 남아 **실행층**을 추가 수정했다. 상세·원복 목록은  
[`tbs_control_2_playback_debug_history_ko.md` §15](tbs_control_2_playback_debug_history_ko.md) 참고.

| 항목 | 적용 |
|------|------|
| LAM `stop()` | `stop_all_*` 제거 → `stop_*_for_context` (화면1=메인 ctx만) |
| N>1 JSON | 메인 스레드 `_submit_screen_json_on_main` (워커 스레드 USD 호출 제거) |
| json_wall | runner+motion idle 후에만 해제; emit 게이트에 motion busy |
| FOUP prim 상태 | `@{screen}:{prim_path}` 스코프 |
| N=1 | 변경 없음 (`_is_multi_viewport_sim` 가드) |

**검증 실패 시**: §15.6 파일 원복 후 재작업.

---

## 1. 목표 (한 줄)

**화면 2개 = 화면 1개 시뮬을 설정만 다르게 2번 돌리는 것과 동일.**  
막대그래프·포트상태·타임테이블·Start/Stop/Reset·JSON 전 step(MOVE, TIMESAMPLES_REPLAY, ROTATE, DELAY 등)이 **화면마다 독립**으로 1화면 때와 같은 품질이어야 한다.

---

## 2. 기능 요구사항

### 2.1 기본 로드

| 항목 | 요구 |
|------|------|
| 최초 Master USD autoload 후 | **2분할 Viewport** + 제어창 **「2화면」 체크** |
| 화면2 초기 설정 | 화면1과 **동일** (`_sim_per_screen_snapshots` 미저장 시 화면1 폴백) |
| 이후 | 화면별 **독립 변경** (기존 저장 UI) |
| 로드 시간 | composed Flatten **prewarm 완료 후** 분할 빌드 — 빈 보조 화면·이중 Flatten 방지 |

설정 SSOT: `sim_control_defaults.py` → `default_viewport_split_count: int = 2`

### 2.2 Start / Stop / Reset

- 버튼 1회 → **활성 화면 전부**에 동일 제어
- 각 화면은 **자기 스냅샷** 기준: 위치 초기화, 포트 t=0·초기 적재, UI 채널 리셋
- 화면1만 되고 화면2만 빠지는 **비대칭 금지**

### 2.3 JSON / 애니메이션 (최우선 품질)

- 2분할에서 **화면1·화면2 모두** 1화면 단독 실행과 **동일**한 MOVE·replay 길이·위치
- step 종류: MOVE, ROTATE, DELAY, **TIMESAMPLES_REPLAY**, USD_TIMELINE, curve 등 **전부**
- 한 화면 interrupt / JSON 시작이 **다른 화면 재생을 끊지 않음**

### 2.4 회귀 금지 (이미 잘 되던 것)

- 2분할 시 (JSON 제외) 막대그래프·포트상태·타임테이블·진행현황
- **Console / Content** 레이아웃 — Dock-only, 절대 좌표로 Console/Content 건드리지 않음
- 2→1 복귀 시 Kit 레이아웃 정상

---

## 3. 이전 구현 실패 원인 (반복 금지)

| 실패 | 원인 | 올바른 규칙 |
|------|------|-------------|
| 2번째 화면 인스턴스 없음 | `ext._sim_viewport_split_count=2`를 **레이아웃 적용 전** 설정 + prewarm 전 `schedule_split_rebuild` | count는 **apply 성공 후**만 2. 최초 분할은 `apply_sim_viewport_split_layout` **1회** |
| Console/Content 깨짐 | 분할 autoload가 레이아웃·rebuild 경로를 건드림 | **Phase 3**만 autoload. 레이아웃 코드(`sim_multi_view` Dock/격자) **변경 최소** |
| JSON 짧게 끊김 | `stop_all_translate_animations()` 등 **전역** stop | **채널(USD 컨텍스트) 스코프** stop 만 — LAM `stop()` 포함 (2026-06-17) |
| JSON 워커 스레드 USD | `_sim_anim_workers_by_screen` 에서 `_start_job` | **메인** `_sim_json_main_queues_by_screen` (2026-06-17) |
| FOUP prim 충돌 | `_FOUP_IN_PROGRESS_PATHS` 경로만 | `@{screen}:{path}` (2026-06-17) |
| 로드 과다 지연 | prewarm + 분할 apply + rebuild **중복** | prewarm 1회 → 캐시 hit → `copy_async`. rebuild는 **이미 분할 활성**일 때만 |

---

## 4. 아키텍처: SimChannel

각 활성 화면 = 독립 채널:

```
채널 N
├── 설정: 스냅샷 N
├── USD: usd_context_name (1=None/메인, 2+=morph_tbs_split_aux_*)
├── 런타임: registry / evaluator / scheduler (보조=SplitScreenRuntime)
├── TBSSimulationEngine + tick 스레드
├── SequenceRunner (모든 step)
└── 애니 테이블 (키 = context + prim_path)
```

**원칙**: `n==1`과 `n==2`는 **같은 코드 경로**, `for channel in active_channels` 루프. 멀티 전용 예외는 레이아웃·USD 오픈에만 한정.

---

## 5. 구현 단계 (반드시 이 순서)

### Phase 1 — 애니/JSON 채널 격리 (레이아웃·autoload **미접촉**)

| 파일 | 작업 |
|------|------|
| `sim_channel_scope.py` (신규) | `iter_sim_channels`, `stop_channel_animations`, `stop_all_channel_animations` |
| `tbs_lam_translate_animation.py`, `translate_animation.py` | `stop_translate_animations_for_context` |
| `tbs_lam_rotate_animation.py`, `rotate_animation.py` | `stop_rotate_animations_for_context` |
| `tbs_lam_sequence_engine.py` | `stop()` → `stop_channel_animations(self._usd_context_name)` |
| `sequence_engine_legacy.py` | `pause()` → 컨텍스트 스코프 stop (전역 stop_all 제거) |
| `control_window.py` | interrupt·prerun seek·`_restore_sim_prim_motion`·Start/Reset → **채널 루프** |

**검증**: 수동으로 「2화면」 체크 → 인스턴스·Console/Content 정상 → 2화면 시뮬 JSON 독립성.

### Phase 2 — Start/Stop/Reset·포트 baseline 채널화

| 작업 |
|------|
| `_restore_all_sim_channels_motion(ext)` — Start/Reset 시 |
| `_ensure_all_channels_port_lot_baseline(ext)` — `ensure_port_lot_authoring_captured(stage)` per context |

(Phase 1과 함께 적용 가능)

### Phase 3 — 기본 2분할 autoload (Phase 1·2 검증 후 또는 동시, **규칙 엄수**)

| 규칙 | 구현 |
|------|------|
| `ebs_control_panel_ui` 초기 count | **`1` 유지** (2 아님) |
| `sim_control_defaults` | `default_viewport_split_count = 2` |
| `notify_tbs_composed_usd_ready_for_split` | prewarm 스케줄 + `schedule_default_viewport_split(ext)` |
| `schedule_default_viewport_split` | `resolve_composed_snapshot_for_split_async` 대기 → `_split_layout_already_active` 아니면 `apply_sim_viewport_split_layout(2)` → 체크박스 동기화 |
| `schedule_split_rebuild_after_master_reload` | **`_split_layout_already_active(ext, n)` 일 때만** (최초 로드 스킵) |
| prewarm `finally` | `ext._tbs_on_composed_prewarm_done_fn(ext)` → 분할 apply 재시도 |

**로드 UX**: 1화면으로 Master 표시 → prewarm 백그라운드 → 완료 즉시 2분할 (Flatten은 1회만).

---

## 6. 수정하면 안 되는 것

- `sim_multi_view` Dock/Console/Content 절대좌표 로직 **대규모 변경**
- `ext._sim_viewport_split_count = 2`를 **init·notify 시점**에 설정
- 최초 로드에 `schedule_split_rebuild_after_master_reload` 무조건 호출
- 멀티 시뮬 UI sink·엔진 tick·타임테이블 **재작성**

---

## 7. 테스트 체크리스트

### A. 레이아웃 (회귀)

- [ ] autoload 후 2분할, Console/Content/EBS 제어창 정상
- [ ] 2번째 화면 합성 인스턴스(`/World/aaa` 등) 표시
- [ ] 2→1 복귀 시 Viewport 전체 너비

### B. 비애니 시뮬 (회귀)

- [ ] 화면별 막대·포트·타임테이블·진행현황

### C. 애니/JSON

- [ ] 1화면 단독 = 이전과 동일
- [ ] 2화면 각각 독립 JSON, 상호 끊김 없음
- [ ] TIMESAMPLES_REPLAY 포함

### D. 제어

- [ ] Start/Stop/Reset → 화면별 위치·포트 UI

---

## 8. 주요 심볼·파일 색인

| 심볼 | 파일 |
|------|------|
| `apply_sim_viewport_split_layout` | `sim_multi_view.py` |
| `schedule_split_composed_snapshot_prewarm` | `tbs_split_composed_loader.py` |
| `composed_split_snapshot_ready` | `tbs_split_composed_loader.py` |
| `_split_layout_already_active` | `sim_multi_view.py` |
| `notify_tbs_composed_usd_ready_for_split` | `control_window.py` |
| `schedule_default_viewport_split` | `sim_split_autoload.py` (신규 권장) |
| `_usd_context_name_for_sim_screen` | `control_window.py` |

---

## 9. 원복 후 재적용 순서 (요약)

1. 이 문서 Phase 1 코드 적용 → **수동 2분할**로 검증  
2. Phase 2 (Start/Reset 루프)  
3. Phase 3 (autoload) — **§3 표의 금지 규칙** 확인 후 적용  
4. §7 체크리스트 전체
