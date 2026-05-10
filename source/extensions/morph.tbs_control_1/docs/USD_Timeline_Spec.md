# USD Timeline 시스템 사양서 (현행)

> **문서 목적**
> - 본 문서는 `morph.tbs_control_1` 확장의 **USD 타임라인 재생 시스템**(USD 내장 프레임 애니메이션 평가/제어/스케줄링) 만을 한정 범위로 정리한 "현행 사실서"이다.
> - 시뮬레이션, FOUP, 분할 화면, 시퀀스 편집기 같은 다른 영역은 본 문서의 직접 주제가 아니며, "USD 타임라인이 거기에서 어떻게 사용되는가" 만 인터페이스 수준으로 언급한다.
> - **이 문서는 살아있는 문서이다.** 이후 USD 타임라인 관련 요구사항은 `§9 요구사항 카드` 에 누적하고, 합의가 끝난 항목만 `§10 변경 이력` 으로 옮긴다.
> - **합의가 끝나기 전에는 어떤 코드도 수정하지 않는다.** 본 문서가 확정된 시점에 코드 수정 작업 단위(Phase)를 시작한다.
> - 버전: **v0.11 (REQ-011 추가: LAM Sequence Editor 가 TBS 와 동일한 4 종 step + JSON schema, USD_TIMELINE 만 인스턴스 드롭다운 차이)**

---

## 1. 한 줄 정의

USD 타임라인 시스템은 "USD 스테이지 안에 박힌 프레임 키 애니메이션"을 `omni.timeline` 인터페이스로 **컨텍스트별 1개의 타임라인**을 통해 재생/일시정지/배속/완료 콜백 형태로 제어하는 얇은 추상 레이어이다.

---

## 2. 모듈 책임 분담

| 파일 | 역할 |
|---|---|
| `morph/tbs_control_1/usd_animation_control.py` | USD 타임라인 직접 제어 핵심. `play_usd_animation` / `stop_usd_animation` / `reset_timeline_to_zero` / 프레임 ↔ 시간 환산 / 배속 best-effort 적용. **컨텍스트별 상태 dict 유지(`_states[k]`)**. |
| `morph/tbs_control_1/sequence_engine.py` | 시퀀스의 `USD_TIMELINE` 스텝에서 위 모듈을 호출. 시작/종료 프레임, per-step `speed_scale`, 완료 콜백을 시퀀스 흐름과 연결. |
| `morph/tbs_control_1/sequence_editor.py` | "USD_TIMELINE" 스텝 편집 UI. 프레임 범위 자동 감지(`resolve_saved_animation_frame_range`) 호출, per-step `speed_scale` 입력. |
| `morph/tbs_control_1/control_window.py` | 수동 재생 버튼 등 UI 진입점에서 위 모듈 호출. (시퀀스 외 단발 재생) |
| `morph/tbs_control_1/sim_multi_view.py` | 분할 화면용 보조 USD 컨텍스트(`morph_tbs_split_aux_N`) 와 그에 묶인 `omni.timeline` 인터페이스가 함께 만들어지도록 하는 인프라. **타임라인 자체는 만들지 않음**, 단지 컨텍스트가 만들어지면서 timeline 도 컨텍스트별로 분리됨. |

---

## 3. 핵심 개념

### 3.1 컨텍스트 ↔ 스테이지 ↔ 타임라인 1:1:1
- `omni.usd.get_context(name)` 로 얻은 컨텍스트는 **자기 자신만의 `Usd.Stage`** 를 가진다.
- `omni.timeline.get_timeline_interface(name)` 은 **그 컨텍스트에 묶인 타임라인 인터페이스** 를 반환한다(가능한 Kit 버전).
- 따라서 분할 화면(보조 컨텍스트 `morph_tbs_split_aux_1..3`)은 각자 **독립된 타임라인** 을 가진다. 본 시스템은 이 사실을 키 분리(`usd_context_name`)로 활용한다.

> 단, 한 스테이지 안에서는 `tl.set_current_time(t)` 가 그 스테이지 전체의 evaluation 시각을 한 값으로 강제하므로, **같은 stage 안의 prim 들을 서로 다른 시각으로 동시 평가하는 것은 본 시스템에서 지원하지 않는다.**

### 3.2 컨텍스트별 상태 캐시 `_states[k]`
- 키: `_timeline_key(usd_context_name)` (빈 문자열/None → `"default"`)
- 값(dict)에 다음 4 종 subscription 핸들 + 토큰을 보관:
  - `_end_fix_sub` — 완료 직후 end_time 으로 다시 고정(다음 재생 시작 직전 튐 방지)
  - `_loop_sub` — `CURRENT_TIME_TICKED` 감지로 loop 처리
  - `_complete_sub` — 1x 모드 완료 감지
  - `_speed_sub` — ≠1x 모드용 wall-clock 기반 `set_current_time` 직접 구동(폴백)
  - `_play_token` — 새 재생/정지가 들어오면 `_speed_sub` 가 자기 토큰과 다른지 확인해 자동 종료

### 3.3 1x 모드 vs ≠1x 모드 (배속 정책)
- 1x: `tl.play()` 후 timeline event stream 의 `CURRENT_TIME_TICKED` 에서 종료 감지.
- ≠1x: `_try_set_timeline_speed()` 가 set/get 라운드트립으로 실제 반영을 확인. **검증 실패 시** 기본 1x 재생 위에 우리가 직접 `set_current_time(start + wall_dt * speed)` 를 매 프레임 덮어씌우는 폴백을 가동(`_speed_sub`).
- 폴백 가동 중에는 timeline event stream 의 complete/loop sub 와 중복 종료를 막기 위해 그 두 sub 를 선제 해제한다.

### 3.4 프레임/시간 환산
- TPS = `tl.get_time_codes_per_seconds()` (없으면 `DEFAULT_TPS=30.0`).
- `frame_to_time(f, ctx)` / `time_to_frame(s, ctx)` 가 컨텍스트 인지 환산을 제공.
- 시퀀스 추정 길이 계산(`_step_duration_sec`)은 `frame_to_time(end - start)` 만 사용한다(컨텍스트 의존성 없음).

### 3.5 프레임 범위 자동 감지
- `resolve_saved_animation_frame_range(usd_context_name)`:
  1) 우선 stage `GetStartTimeCode/GetEndTimeCode`
  2) 폴백 timeline `get_start_time/get_end_time`
- "타임라인이 없거나 컨텍스트가 비어 있으면 None" 을 반환하므로 호출자는 None 처리 책임을 진다.

---

## 4. 외부 인터페이스 (호출자 입장에서의 계약)

본 모듈을 호출하는 모든 측은 아래 4 개 함수만 안다고 보면 된다. **이 시그니처는 "절대 깨면 안 되는" 호환 영역이다.**

```python
# usd_animation_control.py 의 공개 API (현행)
def play_usd_animation(
    start_frame: int = 200,
    end_frame: int = 300,
    loop: bool = False,
    on_completed: Optional[Callable[[], None]] = None,
    usd_context_name: Optional[str] = None,
    speed_scale: float = 1.0,
) -> bool: ...

def stop_usd_animation(usd_context_name: Optional[str] = None) -> None: ...

def reset_timeline_to_zero(usd_context_name: Optional[str] = None) -> None: ...

def resolve_saved_animation_frame_range(usd_context_name: Optional[str] = None) -> Optional[tuple]: ...

def frame_to_time(frame: float, usd_context_name: Optional[str] = None) -> float: ...
def time_to_frame(time_sec: float, usd_context_name: Optional[str] = None) -> float: ...

def is_playing() -> bool: ...   # 주의: 컨텍스트 없는 기본 타임라인만 본다.
```

### 4.1 호출자 인벤토리 (현행)

| 호출자 | 호출 함수 | 컨텍스트 처리 |
|---|---|---|
| `sequence_engine.SequenceRunner._start_step` (USD_TIMELINE 분기) | `play_usd_animation` | `self._usd_context_name` 전달 |
| `sequence_engine.SequenceRunner._stop_step_animations` | `stop_usd_animation` | 동일 |
| `sequence_engine.SequenceRunner.stop` | `stop_usd_animation` + `reset_timeline_to_zero` | 동일 |
| `sequence_engine.SequenceRunner.run` | `stop_usd_animation` + `reset_timeline_to_zero` (시작 직전 0 으로) | 동일 |
| `sequence_engine._step_duration_sec` | `frame_to_time` | 컨텍스트 무관(스케줄 추정용) |
| `sequence_editor` | `frame_to_time` | 컨텍스트 무관(편집용 표시 길이) |
| `control_window.on_play_usd_animation` 등 수동 버튼 | `play_usd_animation` / `stop_usd_animation` | 기본 컨텍스트(보조 컨텍스트는 수동 UI 미연동) |

---

## 5. 시퀀스 안에서의 위치(USD_TIMELINE 스텝)

`SequenceRunner._start_step` 의 `t == "USD_TIMELINE"` 분기 책임:

1. `start_frame`, `end_frame` 검증 (≤ 면 즉시 `_done()`)
2. 옵션 `offset_correction_enabled` 면 코드 기반 MOVE/ROTATE 로 누적된 TBS_OFFSET 을 USD start_frame 평가 결과에 맞춰 보정 (`_apply_world_space_offset_correction`)
3. per-step `speed_scale` × 시퀀스 전체 `speed_scale` 합성 → `combined_sp`
4. `usd_animation_control.play_usd_animation(...)` 호출, 콜백으로 `_done` 연결
5. 호출 실패 시 즉시 `_done()` 으로 다음 스텝 진행

> 주의: `_start_step` 의 다른 분기(`MOVE`, `ROTATE`, `DELAY`)는 USD 타임라인을 건드리지 않는다. **USD 타임라인 시스템과 코드 기반 prim driver(`translate_animation`, `rotate_animation`)는 서로 독립**이며, 한 스테이지에서 같은 prim 에 대해 동시에 둘이 값을 쓰면 시각적 충돌(특히 TBS_OFFSET 경합)이 생긴다는 점만 호출자가 고려한다.

---

## 6. 분할 화면(멀티 컨텍스트)에서의 동작

- 분할이 1 인 경우: 호출자는 `usd_context_name=None` (기본 컨텍스트) 로 사용.
- 분할 ≥ 2 인 경우(`sim_multi_view`):
  - `omni.usd.create_context("morph_tbs_split_aux_N")` 가 보조 컨텍스트를 만든다.
  - 보조 컨텍스트는 자기 stage 와 자기 timeline 인터페이스를 가진다.
  - `SequenceRunner` 에 `usd_context_name="morph_tbs_split_aux_N"` 가 전달되면, `usd_animation_control` 의 모든 함수가 그 컨텍스트의 타임라인만 만진다.
- 보조 컨텍스트가 teardown 되면(`teardown_sim_multi_viewports`), 그 컨텍스트에 묶여 있던 `_states["morph_tbs_split_aux_N"]` 의 sub 들은 컨텍스트 release 후 자연 무효화된다(타임라인 인터페이스가 사라지면 더 이상 호출되지 않음). 다만 진행 중 재생이 있었다면 호출자가 사전 `stop_usd_animation(name)` 으로 정리하는 것이 안전하다.

---

## 7. 동시성 / 라이프사이클 규약

| 시나리오 | 현행 동작 |
|---|---|
| 같은 컨텍스트에서 `play_usd_animation` 가 연달아 호출됨 | 새 호출이 `_play_token` 을 증가시키고, 기존 `_speed_sub` 는 자기 토큰이 아님을 확인하면 자동 종료. `_complete_sub`/`_end_fix_sub`/`_loop_sub` 는 새 호출 시작부 `unsubscribe()`. |
| `stop_usd_animation(name)` | 4 종 sub 모두 unsubscribe + `tl.pause()` + 배속을 1.0 으로 복구 시도. 시간은 그대로(0 으로 안 돌림). |
| `reset_timeline_to_zero(name)` | `tl.pause()` + `set_current_time(0.0)`. (sub 는 건드리지 않음) |
| 다른 컨텍스트끼리 | `_states` 가 키 분리되어 있어 서로 영향 없음. timeline 인터페이스도 컨텍스트별로 분리. |
| 컨텍스트 이름 인자가 None 인데 컨텍스트 없는 환경 | `_get_timeline()` 가 None 반환 → `play_usd_animation` 은 `False` 반환, 다른 함수는 안전하게 no-op. |

---

## 8. 알려진 제약 / 한계 (요구사항 검토 시 반드시 참조)

1. **단일 stage 내 멀티 평가 시각 불가**
   - 같은 stage 의 두 prim 을 `t=10s` / `t=20s` 동시 평가는 본 시스템에서 불가. (`set_current_time` 단일 값)
2. **배속 API 의 비일관성**
   - Kit 버전마다 `set_time_scale` / `set_playback_rate` / `set_speed` / 속성 직접 set 의 적용 결과가 달라 `_try_set_timeline_speed` 가 read-back 검증을 한다. 이 때문에 ≠1x 는 항상 폴백 경로(우리가 매 프레임 set_current_time)로 갈 수 있다.
3. **루프/완료 콜백 중복 위험**
   - 1x 모드의 `CURRENT_TIME_TICKED` 와 ≠1x 모드의 `_speed_sub` 가 동시에 살아 있으면 완료가 두 번 호출되는 레이스가 있어, ≠1x 진입 시 `_complete_sub`/`_loop_sub`/`_end_fix_sub` 를 선제 해제하는 보호가 들어가 있다.
4. **수동 UI 는 기본 컨텍스트만**
   - control_window 의 직접 재생 버튼은 보조 컨텍스트로 라우팅되지 않는다(현행). 보조 컨텍스트 재생은 시퀀스 경로로만 제공된다.
5. **USD 외부에서 키를 들고 있는 prim**
   - Skel/Animation 가 reference 로만 들어오고 master 측에 키가 없는 경우, 본 시스템은 그 키 평가 결과를 시각만 진행시킬 수 있을 뿐(시각이 진행되면 USD/Hydra 가 알아서 평가). 우리가 들고 있는 보간기는 코드 기반 MOVE/ROTATE 전용이라 이 영역에는 관여하지 않는다.

---

## 9. 요구사항 카드 (살아있는 영역)

> 이후 USD 타임라인과 관련된 요구사항은 모두 이 절에 카드 형식으로 추가한다. 합의가 끝나면 `§10 변경 이력` 으로 옮기고 카드 본문은 "Resolved" 로 표시한다.

### REQ-001 — 멀티 USD 로드 + 각자 타임라인 독립 재생 관리

- **출처**: `source/prompt.txt` 132–134
- **요지**: 현재 USD load 는 단일 USD 만 지원. 각각 독립 timeline 을 가진 여러 FBX(→USD) 를 동시에 로드하고, **각자의 타임라인을 독립적으로 재생/제어**할 수 있어야 한다.
- **상태**: Open (가능 여부 분석 단계)
- **분석 메모**: 본 문서 `§3.1` 의 "컨텍스트 ↔ 스테이지 ↔ 타임라인 1:1:1" 사실에 의해 **기술적으로 가능**. 도입 방식 후보는 다음 세 가지로 좁혀진다(자세한 비교는 본 카드의 별첨 분석/대화 응답 참조).
  - **A안: 멀티 컨텍스트 멀티 스테이지** — 분할화면이 이미 쓰는 패턴. USD 별 `omni.usd.create_context("...")` + 각자 stage 로딩. 본 모듈은 인자 변경 없이(`usd_context_name`) 그대로 사용 가능.
  - **B안: master.usd 단일 스테이지 + Reference + 커스텀 PlaybackScheduler** — `set_current_time` 을 쓰지 않고 우리가 prim attribute 를 평가하는 신규 평가기를 옆에 둔다. 본 모듈은 손대지 않는다.
  - **C안: A + B 혼합** — "한 화면에 보이는 큰 무대" 는 master.usd reference, "독립 재생이 꼭 필요한 자산" 은 별도 컨텍스트.
- **결정 필요 항목**:
  1. 화면(=뷰포트)을 USD 마다 따로 보여줄지(A) / 한 뷰포트에 모두 보여줄지(B/C).
  2. 각 USD 의 재생을 동시에 가속/감속(전역 배속) 해야 하는지, 인스턴스별 독립 배속이 필요한지.
  3. 동일 prim 에 두 애니메이션이 동시에 들어오는 케이스가 실제로 있는지(있다면 우선순위 정책 필요).
- **본 모듈에 미치는 영향(예측)**:
  - A안: `usd_animation_control.*` **변경 없음**.
  - B안: 본 모듈 변경 없음. 신규 평가기(`independent_animation.py` 가칭) 옆에 둠.
  - C안: 본 모듈 변경 없음. 호출자가 모드별로 두 시스템을 선택.
- **다음 액션**: 위 결정 항목 1~3 사용자 확인 → 채택안 결정 → Phase 0 부터 도입 계획 수립.

---

### REQ-002 — LAM 별도 확장(`lam_control`) + 다중 USD 로드 + LAM 시퀀스 편집기

- **출처**: `source/prompt.txt` 139–159
- **요지(원문 요약)**:
  1. 지금부터 진행되는 내용은 **기존 시뮬과 완전 분리된 별도 프로젝트**.
  2. 외부(다른 기계) 시뮬 결과를 가져와 애니메이션을 재생.
  3. 각자 timeline 을 가진 FBX→USD 를 **합치지 않고** 각자 timeline 가진 채 동시 로드.
  4. **다중 USD 로드 창** 신규(각 USD 의 timeline 독립 제어).
  5. **LAM 시퀀스 편집기** 신규(기존과 동일 UX 지만 step 마다 어느 USD 의 timeline/prim 인지 지정).
  6. **별도 확장 `lam_control`** 신설. `morph.tbs_control_1` 와 코드 분리. `apps/morph.editor.kit` 에 의존성만 추가.
- **상태**: In Design (결정 1·2·3·4·5 모두 확정)
- **외부 시뮬 결과 형식 가정(예시)**:
  ```json
  {"t": 0.0,  "screen": 1, "kind": "event", "event": "ARRIVED",        "port_id": "EP1", "lot_id": "LOT_001"}
  {"t": 0.0,  "screen": 1, "kind": "step",  "event": "FOUP_PROCESS",   "port_id": "EP1", "label": "FOUP(+Y) EP1", "anim": "", "proc_sec": 1.0,  "anim_sec": 1.0, "detail": "..."}
  {"t": 1.0,  "screen": 1, "kind": "step",  "event": "FOUP_PROCESS",   "port_id": "EP1", "label": "FOUP 공정 EP1", "anim": "", "proc_sec": 47.0, "anim_sec": 0.0, "detail": "..."}
  {"t": 48.0, "screen": 1, "kind": "step",  "event": "FOUP_PROCESS",   "port_id": "EP1", "label": "FOUP(-Y) EP1", "anim": "", "proc_sec": 1.0,  "anim_sec": 1.0, "detail": "..."}
  {"t": 49.0, "screen": 1, "kind": "event", "event": "MOVE_TRANSFERING","from_port_id": "BP1", "to_port_id": "EP1", "lot_id": "LOT_001"}
  ```
- **분석 메모**:
  - 본 사양서 `§3.1` 의 "컨텍스트 ↔ 스테이지 ↔ 타임라인 1:1:1" 사실로 **다중 USD 독립 timeline 은 기술적으로 가능**.
  - 본 사양서가 정의하는 4종 공개 API(`§4`) 는 LAM 측에서 **그대로 재사용 가능**(컨텍스트 이름만 LAM 인스턴스용으로 새로 부여).
  - 단, **물리적 코드 격리** 가 요구되므로 LAM 측은 본 모듈을 import 하지 않고, 같은 의미의 함수를 LAM 확장 안에 자체 보유하는 것을 원칙으로 한다(또는 별도 `morph.tbs_lam_common` 라이브러리 확장으로 분리).
- **결정(확정/미정 표시)**:
  1. **시퀀스 편집기 — step 의 USD 지정 방식** → **B1 확정**
     - step 의 첫 필드에 사용자 친화 ID 드롭다운(`usd_id`: 예 "CharA"). UI 표시명만 alias.
     - JSON 저장 시 `usd_context_name`(예: `lam_inst_CharA`) 으로 정규화.
     - prim 자동완성/트리 검색은 그 USD 의 stage 로 한정.
     - 동시 재생은 기존 `run_with_previous` 그룹 정책으로 표현(여러 USD 가 같은 그룹에 들어가도 됨).
  2. **뷰포트 표시 정책** → **V2 확정** (단 timeline 독립 컨트롤은 P1 평가기로 구현)
     - "여러 FBX→USD 를 **한 화면**(뷰포트 1개)에 모두 보여주되, 각자의 timeline 은 독립 제어".
     - `omni.timeline.set_current_time` 으로는 한 stage 안에서 두 reference 를 서로 다른 시각으로 평가할 수 없음(본 사양 §3.1, §8.1).
     - 따라서 LAM 측은 **Custom Per-USD Evaluator**(P1) 를 신규 모듈로 도입한다(`lam_per_usd_evaluator.py`). 본 모듈(`usd_animation_control`) 은 사용/import 하지 않는다.
     - Evaluator 책임: 각 USD reference 마다 "가상 시각" 을 진행시키며, 그 시각의 attribute 값을 `attr.Get(timeCode)` 로 읽어 master stage 의 over 로 reauthor. 인스턴스별 play/pause/speed/loop/offset 을 관장.
  3. **외부 결과 트리거 모델** → **T1 확정** (Time-driven)
     - 외부 결과는 시간 순서 JSON 라인. runner 가 `t` 오름차순으로 진행하며 `event` 이름에 매칭되는 시퀀스 파일을 시작.
     - **이름 매칭 규약**: `event=event_1` → `data/lam_event_sequences/event_1.json`.
     - 운영 단계에서 event 이름이 다른 형식이 될 수 있으므로, 향후 별도 매핑 표(JSON) 로 우회 가능하게 설계만 열어 둠(이번엔 만들지 않음).
  4. **인스턴스 ID 충돌 정책** → **(b) 확정 — 자동 suffix**
     - 같은 표시 이름을 다시 등록하면 **시스템이 자동으로 `_1`, `_2` …** 를 붙여 등록한다.
       - 예: 첫 등록 `"CharA"` → 그대로 `CharA`. 두 번째 등록 시도 `"CharA"` → `CharA_1`. 세 번째 → `CharA_2`. (사용자가 수동으로 `CharA_1` 을 직접 입력했어도 그 시점에 이미 존재하면 `CharA_2` 로 회피)
     - 사용자가 **명시적으로 새 이름** 을 입력하면(예: `Robot_v2`) 그 이름을 그대로 사용. suffix 는 **충돌 시에만** 부여.
     - 다중 USD 로드 창의 목록에는 표시 이름과 함께 **원본 파일 경로·등록 시각** 을 같이 보여 어느 인스턴스인지 식별 가능하게 한다.
     - **Stage 패널 노출 정책**: §REQ-002 부록 B 참조. 요약하면 — 이 suffix 가 붙은 이름이 **master stage 의 prim 이름** 으로도 그대로 들어가서 Stage 패널에서 시각적으로 확인 가능.
  5. **자산 위치** → **확정**
     - USD 자산: `source/extensions/morph.lam_control/data/usd/`(또는 사용자가 추가하는 임의 절대 경로 허용).
     - 시퀀스: `source/extensions/morph.lam_control/data/lam_event_sequences/`.
     - 외부 결과 샘플: `source/extensions/morph.lam_control/data/lam_external_results/`.

- **추가 핵심 정책 — "현재 위치부터 시작" (LAM 기본 동작)**:
  - TBS 시퀀스(`sequence_engine.SequenceRunner.run`)는 시작 시 `_restore_baseline()` + `port_lot_visibility.restore_port_lot_prims_to_authoring()` 로 prim 위치를 baseline 으로 복원한다.
  - **LAM 시퀀스 러너는 그 정반대를 기본값으로 가진다.** "현재 머문 위치부터 이어서 실행" 이 default(끊김 없음).
  - 구현은 기존 `_start_from_current=True` 와 동등한 동작을 default 로 두는 것이 가장 깔끔. 단 LAM 측은 별도 격리 모듈이므로 동등 의미의 신규 코드를 자체 보유.
  - 사용자가 명시적으로 "이번 실행은 처음으로 리셋" 옵션을 켤 때만 1회성 복원 수행.
- **본 모듈에 미치는 영향**:
  - **0줄 변경**. `morph.tbs_control_1` 의 모든 파일을 손대지 않는 것이 본 카드의 절대 조건.
  - LAM 확장은 본 사양서를 "참고 설계서" 로만 사용한다.
- **신규 확장 토폴로지(확정안)**:
  ```
  source/extensions/morph.lam_control/
    ├ config/extension.toml
    ├ morph/
    │  └ lam_control/
    │     ├ __init__.py
    │     ├ extension.py                 # on_startup / on_shutdown
    │     ├ lam_window.py                # 메인 창(다중 USD 로드 + LAM 시퀀스 편집기 진입)
    │     ├ lam_multi_usd_loader.py      # 각 FBX→USD 를 master stage 의 reference 로 합성/해제
    │     ├ lam_per_usd_evaluator.py     # ★ omni.timeline 안 씀. instance 별 가상 시각 + attr reauthor
    │     ├ lam_sequence_engine.py       # USD_TIMELINE/MOVE/ROTATE/DELAY. USD_TIMELINE 은 evaluator 호출
    │     ├ lam_sequence_editor.py       # LAM 시퀀스 편집기 UI (B1 드롭다운 포함)
    │     └ lam_external_event_runner.py # 외부 JSON 라인 입력 → t 순서대로 event 매칭 시퀀스 트리거
    ├ data/
    │  ├ usd/                            # USD 자산(또는 절대경로 등록 허용)
    │  ├ lam_event_sequences/            # event_N.json (시퀀스 step 배열)
    │  └ lam_external_results/           # 외부 결과 샘플(*.json, t 정렬 배열)
    └ docs/
       └ LAM_Spec.md                     # REQ-002 가 확정되면 본 카드를 이 문서로 분리
  ```
- **시퀀스 편집기 변경 형태(제안 요약)**:
  - 모든 step 의 입력 영역 맨 위에 **"USD 인스턴스 드롭다운"** 추가(B1 채택 가정).
  - prim 자동완성/트리 검색은 **그 USD 의 stage** 로 한정.
  - `USD_TIMELINE` step 은 그 USD 의 stage start/end 프레임을 자동 채움.
  - 동시 재생은 기존 `run_with_previous` 그룹 정책으로 표현(여러 USD 가 같은 그룹에 들어갈 수 있음).
  - JSON 저장 시 정규화: `{"usd_context_name": "lam_inst_CharA", ...}` (UI 표시는 alias).
- **다음 액션**:
  - 미정 항목 4(인스턴스 ID 충돌 정책) 사용자 결정 — (a) vs (b) 중 하나(**§REQ-002 부록 A** 참조).
  - 결정되면 본 카드를 별도 문서 `docs/LAM_Spec.md` 로 분리하고, 본 사양서에는 한 줄 요약과 링크만 남긴다.
  - 그 다음 Phase 0(확장 스켈레톤만 추가, 어디에서도 import 0) → Phase 1(다중 USD 로드 창 + evaluator 단독 동작) → Phase 2(LAM 시퀀스 편집기) → Phase 3(외부 결과 runner) 순서로 진행.

##### REQ-002 부록 A — 인스턴스 ID 충돌 정책 (a) vs (b) 상세 설명

**문제가 되는 상황**

- LAM 다중 USD 로드 창에서 사용자가 각 자산에 붙이는 **표시 이름** 을 `usd_id` 라고 부른다(예: `"CharA"`).
- 내부적으로는 이 이름으로 `usd_context_name`(예: `lam_inst_CharA`) 같은 키를 만들어 타임라인·스테이지·시퀀스가 같은 인스턴스를 가리키게 한다.
- 사용자가 **실수로 또는 의도적으로** 같은 표시 이름 `"CharA"` 로 USD 파일을 **두 번 등록**하려 하면, 시스템은 "지금 등록하려는 것이 기존 `CharA` 와 같은 인스턴스인가, 아니면 새 인스턴스인가?" 를 결정해야 한다. 이 결정 규칙이 **충돌 정책**이다.

**(a) 거부 + 사용자에게 다른 ID 입력 요구**

- 두 번째 등록 시도가 들어오면 **등록을 거부**하고, 메시지 예: «이미 `CharA` 가 있습니다. 다른 이름을 입력하세요».
- **장점**: 사용자가 의도한 것이 "두 개의 다른 캐릭터인데 이름만 같음" 인 경우를 즉시 걸러 줌. 저장된 시퀀스 JSON 안의 `usd_id` 가 항상 사람이 읽기에 유일함.
- **단점**: 사용자가 매번 고유 이름을 지어야 함. 자동화 스크립트로 대량 등록할 때 번거로움.

**(b) 자동 suffix (`CharA` → `CharA_1`, `CharA_2`, …)**

- 두 번째 등록 시 시스템이 자동으로 **`CharA_1`** (또는 내부적으로만 `lam_inst_CharA_1`) 으로 바꿔 등록.
- **장점**: 등록 흐름이 끊기지 않음. 같은 이름을 여러 번 넣어도 모두 살아 남음.
- **단점**: 사용자가 나중에 시퀀스 편집기에서 **어느 인스턴스가 원래 두 번째였는지** 혼동하기 쉬움. 문서/목록 UI 에서 "원본 경로" 나 "등록 순서" 를 반드시 같이 보여 주는 편이 안전.

**선택 가이드**

- 운영에서 **이름 충돌이 거의 없고**, 실수 시 즉시 고치길 원하면 **(a)**.
- 파일 드래그앤드롭 대량 등록·프로토타입 속도를 우선하면 **(b)** + UI 에 원본 경로·등록 시각 표시.

→ **본 프로젝트는 (b) 채택 확정.**

##### REQ-002 부록 B — 인스턴스 이름이 Stage 패널에서 어떻게 보이는가

**Q. (b) 자동 suffix(`CharA_1`) 가 붙으면, 그 이름을 Kit 의 Stage 패널에서도 확인할 수 있는가?**

**A. 가능합니다.** 단 LAM 로더가 **그 이름을 그대로 master stage 의 prim 이름(또는 displayName) 으로 author** 해 주어야 합니다. 그러면 Kit 의 Stage 패널(Window > Stage) 트리에 그대로 보입니다.

**구조 (V2 기준 — 단일 master stage + 각 USD 를 reference)**

```
master_stage  (root layer = master.usd 또는 in-memory anonymous layer)
└─ /World
   ├─ /World/CharA       ← UsdGeom.Xform, references.AddReference("/path/to/robot_v1.usd")
   ├─ /World/CharA_1     ← 두 번째로 등록된 동명 자산 (자동 suffix)
   └─ /World/Robot_v2    ← 사용자가 수동으로 다른 이름을 준 자산
```

이 트리는 Kit 의 **Stage 패널** 에 그대로 보입니다. 각 노드의 **표시 텍스트** 는 prim 이름, 즉 LAM 에서 정한 `usd_id`(suffix 포함)와 같아집니다.

**용어가 4 종류로 나뉘는 점에 주의**

| 이름 종류 | 예 | 어디서 보이나 | 누가 정하나 |
|---|---|---|---|
| `usd_id` (사용자 친화 표시 이름, suffix 포함) | `CharA_1` | LAM 다중 USD 로드 창 목록 | LAM 로더가 자동(중복이면 suffix) |
| **prim 이름 / prim path** | `/World/CharA_1` | **Kit Stage 패널** ✅ | LAM 로더가 master stage 에 author |
| `displayName` 메타데이터(선택) | `CharA_1` 또는 사람이 읽는 라벨 | 일부 Kit 버전의 Stage 패널 컬럼 | LAM 로더가 prim 에 함께 author |
| `usd_context_name` 같은 LAM 내부 키 | `lam_inst_CharA_1` | 사용자에게는 노출 X (LAM evaluator 내부 dict 키) | LAM 내부 |

**LAM 로더의 약속 (설계서)**

1. 사용자가 USD 를 등록할 때마다 LAM 은 다음 순서로 처리한다.
   - 표시 이름 후보를 정한다(사용자 입력값 또는 파일명에서 추출).
   - 이미 같은 이름이 존재하면 `_1`, `_2` … 를 붙여 **충돌 없는 고유 이름** 으로 만든다(suffix 는 충돌 시에만 부여).
   - 그 이름을 **`usd_id`** 로 확정.
2. master stage 에서 **`/World/<usd_id>`** 위치에 `Xform` prim 을 정의(define) 한다.
3. 그 prim 에 `references.AddReference(<해당 USD 파일 경로>)` 로 자산을 붙인다.
4. 가독성을 위해 prim 에 `displayName` 메타데이터도 같이 넣는다(`prim.SetDisplayName(usd_id)`). 일부 Kit 버전 Stage 패널은 이 값을 컬럼으로 보여 줌.
5. LAM 내부 evaluator dict 의 키는 `lam_inst_<usd_id>` 같은 형식으로 별도로 잡되, 사용자는 알 필요 없음.

**결과적으로 사용자가 보게 되는 모습**

- **LAM 다중 USD 로드 창**: `CharA`, `CharA_1`, `Robot_v2` 라는 행이 보이고, 옆에 원본 파일 경로·등록 시각도 같이 표시.
- **Kit Stage 패널**: `/World` 아래에 `CharA`, `CharA_1`, `Robot_v2` prim 이 트리로 보임. 클릭하면 각 USD 의 내용이 펼쳐짐. → **여기서 suffix 도 그대로 확인 가능.**
- **LAM 시퀀스 편집기 step 드롭다운(B1)**: `usd_id` 그대로 선택지. 저장 시 `usd_context_name` 으로 정규화.

**주의사항**

- prim 이름은 USD 식별자 규칙 상 **알파벳/숫자/언더스코어** 만 안전. 사용자가 한글/공백/특수문자를 입력하면 LAM 로더가 **slugify** (예: 공백 → `_`, 한글 → 알파벳 변환 또는 `Asset_<n>` fallback) 후 `usd_id` 로 사용한다.
- `displayName` 에는 원본(한글 포함) 을 그대로 넣을 수 있음. 그러면 Stage 패널 표시는 한글로, 내부 path 는 안전 문자로 분리 가능.
- 같은 USD 파일을 여러 번 등록해도 reference 는 정상 동작(USD 가 instancing 으로 비용을 줄여 줌). 다만 두 인스턴스의 **자체 timeline 상태(가상 시각)** 는 LAM evaluator 가 따로 들고 있다.

---

### REQ-003 — TBS `SequenceRunner`: 실행 시 baseline 복원을 토글로 선택  **[Resolved]**

- **출처**: 사용자 요청(대화 2026).
- **요지**:
  - 현행 `sequence_engine.SequenceRunner.run()` 은 대부분의 경로에서 `self.stop()` 을 호출하고, `stop()` 내부에서 `_restore_baseline()` 으로 prim 위치를 시퀀스 시작 전 저장값으로 되돌린다. 이 동작은 TBS 시뮬에서 **재현 가능한 시작 상태** 를 보장한다.
  - 사용자 요청: **그대로 강제 복원하는 현재 동작은 유지하되**, **원하면 토글로 끌 수 있게** 하여 **현재 머문 위치에서 이어서** 실행할 수 있게 하고 싶다.
- **상태**: **Resolved** — 본 카드의 의도는 이미 코드에 구현되어 있다(아래 §확인).
- **확인 — 이미 구현되어 있는 위치**:
  - `sequence_editor.py` L90~91: `self._start_from_current_model = ui.SimpleBoolModel(False)`, `self._start_from_current_paths_model = ui.SimpleStringModel("")`.
  - `sequence_editor.py` L488~507: 첫 step 입력 영역에 **"현재 위치부터 시작"** 체크박스 + **"대상 경로"** 입력 필드. 기본값 끔 = 현행 baseline 복원 동작 유지.
  - `sequence_editor.py` L1183~1206: JSON 저장 시 첫 step 메타에 `_start_from_current` / `_start_from_current_paths` / `_start_snapshot` 을 기록·로드.
  - `sequence_engine.py` L1382~1402: `run()` 진입 시 첫 step 메타에서 위 3개 필드를 읽어 `self._start_from_current` 에 반영. `True` 이면 `self.stop()` 대신 `pause()` + 정리만 수행하여 `_restore_baseline()` 을 건너뛴다.
  - `sequence_engine.py` L1430~1439: `_start_from_current=False` 면 전체 baseline 복원, `True + paths 비움` 이면 전체 이어 실행, `True + paths 채움` 이면 그 경로만 이어 실행, `True + start_snapshot 있음` 이면 스냅샷 적용 후 그 외만 복원.
- **사용 가이드(요약)**:
  | 시나리오 | UI 조작 | 결과 |
  |---|---|---|
  | 현행 그대로(baseline 복원) | 체크 끔 | 시퀀스 시작 시 prim 위치를 baseline 으로 강제 복원 |
  | 모든 prim 을 현재 자세에서 이어 실행 | 체크 켬 + 경로 비움 | baseline 복원 건너뜀 |
  | 특정 경로만 현재 자세 유지, 그 외는 복원 | 체크 켬 + 대상 경로(공백 구분) 입력 | 입력된 경로만 이어 실행 |
- **본 모듈에 미치는 영향**: 0 (이미 들어 있음).
- **LAM 과의 관계**: LAM 은 기본이 "이어 실행"(체크 켬 등가), TBS 는 기본이 "복원 후 실행"(체크 끔 등가). 두 확장의 UX 용어가 다음 표로 매핑된다.
  | 의미 | TBS 표현 | LAM 표현(기본값) |
  |---|---|---|
  | "현재 위치에서 이어 실행" | 첫 step 메타 `_start_from_current=true` | 항상 (기본 동작) |
  | "이번 한 번만 baseline 복원" | 첫 step 메타 `_start_from_current=false` (기본) | LAM 측 1회성 reset 옵션(추후 §REQ-004 에서 명세) |
- **다음 액션**: 없음(Resolved).

---

### REQ-004 — Unified Runtime Architecture (Direct + Composition)

- **출처**: 본 사양서 v0.6 합의(LAM 신규 확장의 기반 아키텍처).
- **요지**:
  - LAM 측이 USD 자산을 어떻게 받든(개별 USD 직접 로드 / master.usd 안의 reference / payload), **런타임은 동일한 추상화** `AnimationInstance` 를 본다.
  - `AnimationInstance` 의 **고유 키는 master stage 안의 `prim_path`**. 사용자 친화 별칭 `instance_id` (예: `CharA_1`)는 `prim_path` 의 alias 이며 customData 로 prim 자체에 박힌다.
  - `omni.timeline` 은 master stage 단일 평가 시각만 다룰 수 있어 인스턴스별 독립 timeline 을 표현할 수 없다 → LAM 은 **Custom Runtime Evaluator (P1)** 를 자체 보유한다(`omni.timeline` 미사용·미import).

- **5-Layer 아키텍처**(파일·디렉터리 1:1 매핑은 §REQ-002 토폴로지와 동일):
  | 레이어 | 책임 | 모듈(가칭) |
  |---|---|---|
  | **L1 Asset Loading** | 외부 USD 를 master stage 의 reference 로 추가/제거. master.usd 저장. | `lam_master_stage.py`, `lam_multi_usd_loader.py` |
  | **L2 Composition Discovery** | master stage 를 traverse 하여 "재생 가능한 instance 후보" 발견. R1·R2·R3 규칙 적용. | `lam_composition_discovery.py` |
  | **L3 Animation Instance Registry** | `prim_path` 유일성 강제, `instance_id` 자동 suffix. `AnimationInstance` 객체의 단일 진실 원천. | `lam_instance_registry.py` |
  | **L4 Playback Scheduler** | start/stop/pause/resume/speed/loop API. step 호출자(L≈시퀀스 엔진) 에게 단일 진입점. "이어 실행/리셋" 정책 관리. | `lam_playback_scheduler.py` |
  | **L5 Runtime Evaluator** | 매 프레임 heartbeat. 인스턴스의 `virtual_time` 을 진행시키고, 그 시각에서의 attribute 값을 master stage 의 root layer 에 over 로 reauthor. **`omni.timeline` 미사용**. | `lam_runtime_evaluator.py` |

- **`AnimationInstance` 데이터 모델(필드 고정)**:
  ```python
  @dataclass
  class AnimationInstance:
      prim_path: str            # 1차 키 (master stage 안 위치)
      guid: str                 # 영구 고유 ID — prim 의 lam:guid customData 와 동일
      instance_id: str          # 사용자 친화 alias (suffix 포함). prim displayName / lam:instance_id
      source_asset: str         # 자산 USD 경로(상대/절대 — §REQ-005 정책 따름)
      discovered_by: str        # "user_register" | "composition_discovery"
      virtual_time: float = 0.0
      speed: float = 1.0
      loop: bool = False
      state: str = "stopped"    # "stopped" | "playing" | "paused"
      offset_sec: float = 0.0
      range: tuple = ("full", 0.0, 0.0)  # ("full" | "frames" | "ratio", start, end)
  ```

- **L2 Composition Discovery 규칙(R1·R2·R3)**:
  - **R1**: prim 의 customData 에 `lam:instance == True` 가 있으면 그 prim 을 인스턴스 후보로 등록(LAM 이 직접 author 한 prim).
  - **R2**: prim 이 reference/payload 를 가지고, 그 reference target USD 가 timeSamples 를 보유하면 인스턴스 후보로 등록.
  - **R3**: prim 의 모든 `Xformable` attribute 중 어느 하나라도 timeSamples 를 가지면 인스턴스 후보로 등록(외부에서 author 된 자산 흡수).
  - 우선순위: R1 > R2 > R3. 동일 prim 이 다중 규칙으로 발견되어도 인스턴스는 1개만 등록(prim_path 유일).

- **"이어 실행" 정책 (LAM 기본)**:
  - L4 의 `start(prim_path, …)` 가 호출되어도 `virtual_time` 을 0 으로 리셋하지 **않는다**(현재 시각부터 진행).
  - 명시적 1회성 reset 은 `start(prim_path, reset=True)` 옵션으로만 발동.
  - master stage 의 prim attribute 는 reauthor 되지만 **TBS_OFFSET 같은 외부 모듈의 메타키와는 충돌하지 않는다**(LAM 은 자기만의 `lam:reauthored_at` customData 로 관리).

- **회귀 보호**:
  - 본 카드 어디에서도 `morph.tbs_control_1` 의 모듈을 import 하지 않는다(REQ-002 0줄 변경 원칙 강화).
  - 본 카드 어디에서도 `omni.timeline` 의 `set_current_time` 을 호출하지 않는다(§3.1 멀티 평가 불가 한계 회피).

- **상태**: **In Design — 코드 스켈레톤 단계 진입 (Phase 0)**.
- **다음 액션**:
  - Phase 0: 본 카드의 5 모듈을 신규 확장 안에 **빈 스켈레톤** 으로 추가(어디서도 import 안 됨 → 회귀 0).
  - Phase 1: L1·L3 우선 동작(USD 1개 등록, prim 트리 보임).
  - Phase 2: L4·L5 가벼운 reauthor 로 "1개 인스턴스 가상 시각 진행".
  - Phase 3: L2 Discovery 로 master.usd 재오픈 시 자동 복원.

---

### REQ-005 — Master USD Persistence Policy

- **출처**: REQ-004 와 짝(저장·재오픈 의미를 명확히 하지 않으면 시퀀스 JSON 의 `prim_path` 가 깨진다).
- **요지**: LAM 이 author 한 prim 트리(reference + customData + displayName) 가 어떤 시점·어떤 layer 에 어떤 형태로 저장되는지 정한다.

- **결정 항목과 권장값**:
  | 항목 | 후보 | 권장 |
  |---|---|---|
  | **P-1 저장 정책** | (A) 사용자 명시 Save 만 / (B) USD 등록·해제 시 즉시 자동 Save / (C) 종료 시 자동 Save | **(A) 명시 Save** + (보조) 종료 시 사용자 확인 다이얼로그 |
  | **P-2 자산 경로 모드** | (A) 절대 / (B) master.usd 기준 상대 / (C) 자산 라이브러리 매핑 | **(B) 상대** 기본, 사용자가 절대 경로 입력 시 그대로 보존 |
  | **P-3 편집 대상 layer** | (A) Session Layer / (B) Root Layer | **(B) Root Layer** — 저장 시 그대로 직렬화되어야 다음 세션에서 동일 prim 재발견 가능 |
  | **P-4 자동 재오픈** | (A) 마지막 master.usd 자동 / (B) 빈 master 로 시작 | **(B)** 기본. (A) 는 옵션으로 토글 |

- **저장되는 메타데이터 규약(prim 단위)**:
  - `customData["lam:instance"] = True`
  - `customData["lam:guid"] = "<UUID4>"`
  - `customData["lam:instance_id"] = "<usd_id>"` (suffix 포함 최종 이름)
  - `customData["lam:source_asset"] = "<path>"` (P-2 정책에 따른 표기)
  - `customData["lam:added_at"] = "<ISO 8601>"`
  - `prim.SetDisplayName(usd_id)` (Stage 패널 컬럼 호환)

- **저장 동작 흐름**:
  1. 사용자가 `Save Master…` 누름 → 파일 다이얼로그.
  2. 처음 저장이면: 새 root layer 생성 → `master.usd` 로 저장 → 이후 stage 의 root layer 로 set.
  3. 이미 root layer 가 있는 경우: root layer 에 그대로 flush.
  4. 저장 후 메모리에는 변화 없음(LAM evaluator 의 `virtual_time` 은 저장 대상 아님).

- **로드 동작 흐름**:
  1. 사용자가 `Open Master…` 누름.
  2. master stage 컨텍스트 생성 → 해당 USD 를 root layer 로 마운트.
  3. L2 Composition Discovery 자동 실행 → R1 우선으로 인스턴스 등록(lam:guid·lam:instance_id 등 메타 그대로 채움).
  4. L3 Registry 가 prim_path 유일성 검증(`prim_path` 충돌이 있을 수 없는 구조라 항상 통과).

- **상태**: **Decided — 코드 반영 단계**.
- **본 모듈에 미치는 영향**: 0 (LAM 측 신규 모듈에서만 구현).
- **다음 액션**: Phase 0 의 `lam_master_stage.py` 스켈레톤에 P-1/P-2/P-3/P-4 hook 자리만 박아 둠.

---

### REQ-006 — Sequence Step ↔ Animation Instance Binding (4-튜플 ref + Resolver)  **[Decided]**

- **출처**: 사용자 질문 — "매번 새 FBX 가 들어와서 경로가 달라지는데 시퀀스 JSON 은 어느 키로 USD 를 찾아갈 것인가?"
- **요지**: 시퀀스 step 의 USD/Timeline 참조를 단일 키로 박지 않고 **4-튜플(`prim_path`, `guid`, `instance_id`, `source_asset`) + 우선순위 Resolver** 로 관리한다. 자동 갱신·자동 매칭으로 자산 갱신 시에도 시퀀스가 살아남는다.

- **step 안의 참조 블록(JSON 스키마)**:
  ```json
  {
    "type": "USD_TIMELINE",
    "ref": {
      "prim_path":     "/World/CharA_1",
      "guid":          "a3b9-7f12-8e44-…",
      "instance_id":   "CharA_1",
      "source_asset":  "./assets/CharA.usd"
    },
    "play": {
      "range_mode":   "full",
      "start_frame":  0,
      "end_frame":    120,
      "ratio_start":  0.0,
      "ratio_end":    1.0,
      "speed":        1.0,
      "loop":         false
    }
  }
  ```

- **Resolver 우선순위(Q-1 권장 확정)**: `guid → prim_path → instance_id → source_asset` 순으로 시도. 첫 성공이 매칭. 매칭 후 4 키를 **자동으로 새 값으로 덮어 씀**(Q-3 권장 확정).
- **Range 기본 모드(Q-2 권장 확정)**: `full` (자산이 바뀌어도 stage start/end 자동 사용 → 끊김 가장 적음).
- **상태 배지**:
  - 🟢 OK — 1·2 순위 매칭
  - 🟡 AUTO — 3·4 순위 매칭(자동 갱신 발생)
  - 🔴 MISSING — 매칭 실패. step 회색 + `Re-bind…` 다이얼로그
- **Re-bind 동작**: 사용자가 인스턴스 드롭다운에서 한 번 선택 → 4 키 모두 갱신 → 다음 저장 시 JSON 에 반영.

- **자산 갱신 시나리오 — 어떤 키로 살아남는가**:
  | 시나리오 | 깨지는 키 | 살아남는 키 |
  |---|---|---|
  | 같은 캐릭터 v2 로 자산 교체(자리 동일) | guid | prim_path → 매칭 후 guid 갱신 |
  | master 안에서 prim 이름 변경/이동 | prim_path | guid → 매칭 후 prim_path 갱신 |
  | 다른 master.usd 를 열었음(인스턴스 없음) | 전부 | 매칭 실패 → MISSING → Re-bind |
  | 사용자가 alias 만 바꾸었음 | instance_id | guid 또는 prim_path |

- **본 모듈에 미치는 영향**: 0 (LAM 측에서만 구현).
- **다음 액션**:
  - Phase 0 에서 `lam_id_resolver.py` 스켈레톤에 우선순위 함수만 박아 둠.
  - Phase 2 에서 LAM 시퀀스 편집기 step 입력 영역에 인스턴스 드롭다운 + 상태 배지 + Re-bind 버튼 결선.
- **결정 확정값(Q-1·Q-2·Q-3)**:
  - Q-1 = `guid → prim_path → instance_id → source_asset`
  - Q-2 = `full`
  - Q-3 = 매칭 성공 시 ref 자동 갱신, 다음 저장에 반영

---

### REQ-007 — LAM Viewport 정책: 화면 1개만 보이게 (결정 A → A' 재설정)  **[Decided]**

- **출처**: 2026-05-10 prompt.txt (14-20). 사용자가 "하나의 viewport 에 USD 가 보여야 하는데 두 개가 떠 있다" 고 지적. 이후 검증에서 일부 Kit 빌드의 `viewport.usd_context_name` setter 가 silent 하게 무시되어 default viewport·Stage 패널이 LAM 의 prim 을 못 보는 환경이 확인됨.
- **요지**: LAM 자산이 별도 viewport 가 아니라 **사용자가 늘 보던 default viewport·Stage 패널·Property 패널** 모두에 자동으로 보이도록 한다.
- **결정 (A) — 폐기**: ~~default viewport 의 `usd_context_name` 을 별도 LAM 컨텍스트(`morph_lam_master`) 로 마운트~~. 일부 빌드에서 setter 가 무시되어 기본 패널들에 LAM 이 안 보이는 문제가 재현됨.
- **결정 (A') — 채택**: LAM 도 **default 컨텍스트(`""`) 자체를 사용**한다. (`LAM_MASTER_CONTEXT_NAME = ""`) → Kit 의 모든 기본 패널(Viewport / Stage / Property) 이 자동으로 LAM 의 prim 을 본다.
- **구현 위치**: `morph/lam_control/lam_master_stage.py` (상수 변경, `ensure_context()` 가 default context 의 stage 가 비어 있을 때만 새로 만듦, upAxis 를 Z 로 명시), `morph/lam_control/lam_viewport.py` (context 가 빈 문자열이면 mount/폴백 둘 다 no-op, `is_default_visible()` 의미 확장).
- **트레이드오프**:
  - tbs_control_1 의 `[USD Load]` 가 default stage 를 새로 열면 LAM 의 author 가 같이 사라진다. → LAM Window 에 안내 라벨 추가.
  - LAM 과 TBS 가 같은 stage 위에 prim 들을 author 한다(서로 다른 prim_path 면 충돌 없음).
- **선택 가능한 폴백**: 사용자가 [LAM Viewport 강제 열기] 를 누르면 별도 viewport 창을 따로 띄울 수 있음(`LamViewport.open_dedicated()`).
- **본 모듈(`tbs_control_1`)에 미치는 영향**: 0줄 변경 원칙 유지.

#### REQ-007 보완 단서 (2026-05-10 핫픽스 4)

- LAM 의 `RuntimeEvaluator` 가 `omni.timeline.set_current_time(seconds)` **만** 호출해 stage 평가를 트리거한다(매 frame 마지막 playing 인스턴스의 vt 가 winner). play/pause 같은 timeline 제어는 호출하지 않는다.
- 사유: 사용자 자산의 timeSamples 가 reference 안에 들어 있어 `prim.GetAuthoredAttributes()` 로는 잡히지 않고, 잡혔어도 default value author 로는 reference timeSamples 를 마스킹할 수 없음(=가설 A+B). 이로 인해 attribute reauthor 만으로는 화면 변화가 발생하지 않음.
- 기존 attribute reauthor 코드는 호환 보존을 위해 그대로 두되, `wrote=0` 인 자산에서는 자연스럽게 no-op.
- master stage 의 `endTimeCode` 가 `eval_seconds * tps` 보다 작으면 자동 확장(1 회 진단 로그).
- **TBS 영역(`morph.tbs_control_1`)에 미치는 영향**: 0줄 변경 원칙 유지(LAM evaluator 만의 `omni.timeline.set_current_time` 호출은 TBS 의 `usd_animation_control.py` 의 `play()/pause()` 와 별개 인터페이스).
- 다중 인스턴스 동시 재생 시 마지막 set 인스턴스의 vt 가 winner — 인스턴스별 다른 vt 동시 평가가 필요해지면 별도 stage(sublayer) 분리 모델이 필요(별도 REQ).

#### REQ-007 추가 보완 (2026-05-11 핫픽스 5)

- 핫픽스 4 의 `set_current_time` 은 stage 전체에 단 하나의 currentTime 만 설정하므로, **한 USD_TIMELINE step 의 인스턴스만 trigger 했음에도 다른 LAM 인스턴스의 reference 도 같은 timeCode 로 평가되어 같이 재생되는 문제** 가 발생.
- 해결: LAM `RuntimeEvaluator` 가 인스턴스 `state` 전환 시점에 reference 의 `Sdf.LayerOffset` 을 author 한다.
  - `playing` 진입: `Sdf.LayerOffset(0, 1)` — stage current time 따라 정상 평가.
  - `stopped` / `paused` 진입: `Sdf.LayerOffset(freeze_tc, 0)` — `scale=0` 이라 stage current time 변화와 무관하게 같은 frame 에 frozen. `freeze_tc = (virtual_time + offset_sec) * tps`.
- 매 frame 호출되지만 **실제 author 는 직전 frame 과 state 가 달랐을 때만** 일어난다(비용 ↓).
- 새로 등록된 인스턴스는 default `state="stopped"` → 다음 update tick 에 자동 freeze 적용.
- **단일 활성 인스턴스** 케이스(LAM Sequence Editor 의 일반 사용 패턴): 두 USD 모두 화면에 보이지만 step 으로 trigger 한 인스턴스만 진행 — REQ 의 본래 의도("여러 USD 동시 표시 + 각자 독립 재생") 충족.
- **다중 활성 인스턴스** 동시 재생: 둘 다 `scale=1` 이라 같은 stage current time 으로 평가됨(마지막 set 인스턴스의 vt 가 winner). 인스턴스별 다른 vt 동시 평가는 별도 stage(sublayer) 모델이 필요(별도 REQ).
- 콘솔 진단 로그 형식: `[LAM/L5] freeze sync prim=... PLAY/FREEZE offset=... scale=... (state old → new)` — state 전환 시점 1 회.

#### REQ-007 재설계 (2026-05-11 핫픽스 6) — Per-Instance Layer Offset Mapping

- 핫픽스 5 의 단순 freeze 가 다중 활성 케이스에서 다시 같이 재생되는 한계 → **per-instance `Sdf.LayerOffset` 매핑** 으로 재설계.
- **모델**: `master_tc = master_seconds * tps_master`(evaluator 의 wall clock), `inst_tc(t) = offset_i + master_tc(t) * scale_i`.
  - `playing` 진입 시 한 번만 author: `scale_i = inst.speed`, `offset_i = (inst.virtual_time*tps + asset_start_tc) − start_master_tc * inst.speed`. 이후 wall clock 진행 → `inst_tc` 자동 sync.
  - `stopped`/`paused` 진입 시: `scale_i = 0`, `offset_i = freeze_inst_tc`. master_tc 진행과 무관.
  - loop wrap / speed 변경 → mapping 시그니처 변경 → 자동 재author.
- **다중 활성 인스턴스 동시 재생**: 각자 다른 `(offset, scale)` 이라 같은 master_tc 에서도 서로 다른 inst_tc 로 평가 = **진정한 independent playback**.
- **`omni.timeline.set_current_time`** 은 wall clock master_seconds 만 driving(인스턴스 vt 와 무관).
- **모든 인스턴스 `stopped`** 일 때는 stage 보존 → 사용자 슬라이더 조작 가능. `playing` 인스턴스가 1 개라도 생기면 evaluator 가 매 frame stage time 진행.
- **`_has_lam_reference(stage, prim_path)` 가드**: `lam:instance` customData 가 박힌 prim 만 layerOffset 변경 → TBS 등 다른 자산 평가 경로 영향 0.
- **MOVE/ROTATE/DELAY 무관**: prim 자체의 `TBS_OFFSET` XformOp 변경이라 reference layerOffset 과 무관.
- 콘솔 진단 로그 형식: `[LAM/L5] mapping prim=... PLAY/FREEZE offset=...tc scale=... (...)` — 시그니처 변경 시점에만.

---

### REQ-011 — LAM Sequence Editor: TBS 와 동일한 4 종 step + JSON schema (USD_TIMELINE 만 인스턴스 드롭다운 차이)  **[Decided]**

- **출처**: 2026-05-10 사용자 요청 — "시퀀스 편집기에서는 tbs 시퀀스 편집기와 동일하게 usd 타임라인 뿐 아니라 rotate, move, delay 등등 동일하게 스탭형식으로 추가가 되어야 한다. 단지 타임라인에서 인스턴스선택부분만 제외하고 모든것이 동일하게 동작하도록 하고 json 도 마찬가지로 스탭으로 생성하고 json 으로부터 스탭을 생성하기도 해야 한다."
- **요지**:
  1. LAM Sequence Editor 의 step 종류 = TBS 와 동일 4 종(`USD_TIMELINE / MOVE / ROTATE / DELAY`).
  2. 각 종별 UI 행의 필드·기본값·JSON schema 모두 TBS 와 동일.
  3. **차이점은 USD_TIMELINE step 한 곳에만** — UI 맨 위에 "LAM 인스턴스 드롭다운" 한 줄과 상태 배지(● OK / ● AUTO / ● MISSING) + Re-bind 버튼이 추가되어, 선택한 인스턴스의 정보를 step["ref"] 4-tuple(REQ-006) 로 박아 둔다.
  4. JSON Save/Load 도 동일 schema. USD_TIMELINE 만 추가로 `ref` 필드를 가지며, MOVE/ROTATE/DELAY 의 `prim/duration/dx-dz/rx-rz/auto_pivot_world_center/user_axis_rotate/pivot_w*/run_with_previous/step_delay_ms/hide_enabled/hide_prims/_start_from_current/_start_from_current_paths/_start_snapshot` 등 모든 키는 TBS 와 동일하게 보존·해석한다.
  5. Run/Stop 은 Editor 의 background thread 에서 `LamSequenceRunner.run()` 호출. Pause/Resume 은 추후 추가(P3 시점에는 미포함).
- **구현 위치 (LAM 측, REQ-002 0줄 변경 원칙 준수)**:
  - `lam_translate_animation.py` (신설) — TBS `translate_animation.py` 와 동일 동작. `_OFFSET_SUFFIX="TBS_OFFSET"` 의 TranslateOp 에 누적 보간.
  - `lam_rotate_animation.py` (신설) — TBS `rotate_animation.py` 동일 의미. 3 모드(simple / lock_world_center / world_pivot_euler).
  - `lam_offset_correction.py` (신설) — TBS `_apply_world_space_offset_correction` 동일 수식. USD_TIMELINE 시작 직전 `TBS_OFFSET` 두 op 재계산.
  - `lam_hide_helper.py` (신설) — TBS hide refcount + delayed unhide(0.2s) 동등.
  - `lam_sequence_engine.py` (재구성) — 4 종 step 분기 + `run_with_previous` 그룹 + `step_delay_ms` (initial wait, follower offset, group-to-group) + 첫 step `_start_from_current` / `_start_snapshot` 메타 처리(`_apply_start_snapshot` 가 m16 을 TBS_OFFSET 두 op 로 분해 author).
  - `lam_sequence_editor.py` (전면 재작성) — 4 종 ComboBox + 각 종 UI 행 + USD_TIMELINE 인스턴스 드롭다운/상태/Re-bind + 'Stage 선택에서 prim 가져오기' + JSON Save/Load (FilePickerDialog) + 첫 step `_start_from_current` 메타 UI + Snapshot 캡처/비우기 버튼 + Run/Stop background thread.
- **호출 모델**: `LamSequenceRunner.run()` 은 동기 차단형. 시퀀스 편집기·`LamJsonTestWindow`·`LamExternalEventRunner` 모두 별도 thread 에서 호출(이미 그렇게 구성). USD_TIMELINE 은 `Scheduler.start()` 후 estimated duration 만큼 wall-clock sleep, MOVE/ROTATE 는 animator(=`omni.kit.app` update event stream) 호출 후 duration sleep, DELAY 는 sleep.
- **TBS 와의 차이 (LAM architecture 차)**:
  - LAM 의 `_start_from_current` 자체는 거의 default 동작 (LAM 은 baseline 강제 복원이 없음). 단 `_start_snapshot` 의 m16 은 시작 시 TBS_OFFSET 두 op 로 분해 author 되어 의미가 살아남는다 → UI 라벨로 안내.
  - `offset_correction_enabled` 는 TBS 와 동일 의미·동일 수식. evaluator(`lam_attribute_reauthor`) 가 reauthor 하는 attr 과 겹치는 경우(자산 자체가 root prim 의 transform 에 timeSamples 를 가지는 경우) 효과가 가려질 수 있음 — TBS 의 한계와 동일.
- **본 모듈(`tbs_control_1`)에 미치는 영향**: 0줄 변경, 0 import, 0 `omni.timeline` import.

---

### REQ-010 — LAM 자산 upAxis 자동 보정  **[Decided]**

- **출처**: 2026-05-10 사용자 보고 — "좌표가 이상하게 눕혀져서 객체가 로드되고 있어." Y-up 자산을 Z-up master stage 에 reference 한 결과.
- **요지**: 자산 USD 의 `UsdGeom.GetStageUpAxis` 와 master stage 의 upAxis 가 다르면, reference prim 에 `RotateX(±90)` 보정을 자동 author 한다.
- **결정**: `lam_multi_usd_loader.add_usd()` 가 자산 reference attach 직후 다음 절차를 수행한다.
  1. `read_asset_up_axis(source_asset)` 로 자산 upAxis 를 'Y' / 'Z' 로 분류.
  2. master stage 의 upAxis 와 비교.
  3. 다르면 reference prim 에 `UsdGeom.Xform.AddRotateXOp(opSuffix="lamUpAxisFix")` 로 다음 회전 author:
     - master Z-up + asset Y-up: **+90°**
     - master Y-up + asset Z-up: **−90°**
  4. customData 에 `lam:asset_up_axis` / `lam:master_up_axis` 도 박아 디버깅·재로드 시 추적 가능.
- **구현 위치**: `morph/lam_control/lam_multi_usd_loader.py` (`read_asset_up_axis`, `_stage_up_axis`, `_author_up_axis_fix`).
- **재로드(open_master)**: customData 에 보정 정보가 박혀 있으므로 다음 세션에서도 그대로 보임. 새 자산이 추가되면 그 prim 만 자동 보정.
- **본 모듈(`tbs_control_1`)에 미치는 영향**: 0.

---

### REQ-008 — LAM Window UX 단순화 (가이드형 두 흐름 + Master USD 표기 안내)  **[Decided]**

- **출처**: 2026-05-10 prompt.txt (14-20). "지금 USD load 창 구조가 복잡해서 어떻게 로드해야 하는지조차 모르겠다", "master.usd 는 예시이고 파일명은 임의여야 한다."
- **요지**:
  1. 첫 화면을 **시작 방법 두 가지**(① 새로 시작 — USD 추가해 합성 만들기 / ② 기존 합성 USD 열기)로 가이드형 분리.
  2. **"Master USD" 표기 유지** + 파일명은 임의(예: master.usd 는 예시) 라는 한 줄 안내문구 추가.
  3. 각 경로 입력 옆에 **파일 다이얼로그 버튼** 부여(Add USD / Open Master / Save Master As). `omni.kit.window.filepicker.FilePickerDialog` 사용.
  4. 등록된 인스턴스·도구·외부 시뮬 결과는 `CollapsableFrame` 으로 접고 펼침. 외부 결과는 기본 접힘.
  5. **LAM Window 가 뜨는 시점에 LAM Sequence Editor 도 같이 자동으로 연다.**
- **구현 위치**: `morph/lam_control/lam_window.py`.
- **본 모듈에 미치는 영향**: 0.

---

### REQ-009 — JSON 테스트 창 신설 (시퀀스 편집기와 별개, 연쇄 실행 검증)  **[Decided]**

- **출처**: 2026-05-10 prompt.txt (7-11). "JSON 이 실행되는 도중 다른 JSON 이 실행되는 시나리오를 시퀀스 편집기 말고 별도 창에서 손쉽게 검증하고 싶다."
- **요지**:
  1. LAM Window 의 **도구** 섹션에서 진입하는 별도 창 `LamJsonTestWindow`.
  2. 스텝 종류 2개:
     - `ADD_JSON` — 드롭다운으로 `lam/lam_event_sequences/*.json` 중 하나 선택 → `LamSequenceRunner.run(steps)` 호출. USD_TIMELINE step 은 `Scheduler.start()` 만 하고 즉시 반환하므로 다음 step 으로 빨리 넘어간다.
     - `DELAY` — wall-clock sleep (백그라운드 스레드, 100ms 단위 폴링으로 Stop 반응성 확보).
  3. Run 은 별도 데몬 스레드에서 step 들을 순차 처리, Stop 은 즉시 중단 신호.
  4. 같은 prim 의 재생이 진행 중일 때도 **다른 JSON 의 step 이 동시에 시작될 수 있다** — REQ-004 가상 시각 분리 정책으로 인스턴스마다 독립.
- **구현 위치**: `morph/lam_control/lam_json_test_window.py`.
- **본 모듈에 미치는 영향**: 0.

---

### REQ-XXX — (앞으로 여기에 누적)

- **출처**:
- **요지**:
- **상태**: Open
- **분석 메모**:
- **결정 필요 항목**:
- **본 모듈에 미치는 영향**:
- **다음 액션**:

---

## 10. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v0.1 | (초안) | 현행 USD 타임라인 시스템 사실 정리. REQ-001(멀티 USD 로드) 카드 등록(Open). |
| v0.2 | (초안) | REQ-002(LAM 별도 확장 + 다중 USD 로드 창 + LAM 시퀀스 편집기) 카드 등록(Open). 본 모듈 0줄 변경 원칙 명시. |
| v0.3 | (초안) | REQ-002 에 결정 1·2·3 확정(B1 / V2+P1 평가기 / T1). "현재 위치부터 시작" LAM 기본 동작 정책 명문화. 신규 데이터 폴더 신설(`data/lam_event_sequences/event_1~5.json`, `data/lam_external_results/sample_external_result.json`). 코드 0줄 변경. |
| v0.4 | (초안) | REQ-002 부록 A: 인스턴스 ID 충돌 정책 (a)/(b) 상세 설명. REQ-003 등록: TBS SequenceRunner baseline 복원 토글(기본은 현행 유지). 코드 0줄 변경. |
| v0.5 | (초안) | REQ-002 결정 4 = **(b) 자동 suffix** 확정. 부록 B 추가: suffix 가 붙은 `usd_id` 가 master stage 의 prim 이름·displayName 으로 author 되어 **Kit Stage 패널에서 그대로 확인 가능**함을 명시. slugify·동일 USD 다중 등록·내부 키 분리 규약 명문화. 코드 0줄 변경. |
| v0.6 | 2026-05-10 | REQ-004(Unified Runtime Architecture) · REQ-005(Master USD Persistence Policy) 정식 카드화. 5-Layer 구조와 `AnimationInstance` 데이터 모델, R1·R2·R3 discovery 규칙, P-1~P-4 저장 정책 권장값 확정. 코드 0줄 변경. |
| v0.7 | 2026-05-10 | REQ-003 **Resolved**(이미 sequence_editor/sequence_engine 에 `_start_from_current` 플래그로 구현된 것을 카드에 명시). REQ-006 **Decided**(시퀀스 step 의 USD 참조를 4-튜플 `prim_path/guid/instance_id/source_asset` + 우선순위 Resolver 로 관리, Q-1·Q-2·Q-3 권장값 확정). LAM 신규 확장 `morph.lam_control` 의 Phase 0 스켈레톤(파일 트리 + 빈 모듈) 코드 추가. `morph.tbs_control_1` 코드 0줄 변경. |
| v0.8 | 2026-05-10 | LAM Phase 1~4 실코드 반영. **Phase 1**: LAM 전용 Viewport 자동 생성(`lam_viewport.py`), `LamWindow.show()` 시점에 master context 보장 + Save/Open 후 root layer EditTarget 강제. **Phase 2**: L5 attribute reauthor 실구현(`lam_attribute_reauthor.py`) — per-instance `attr.Get(timeCode) → attr.Set(val)` 로 root layer default 에 박아 USD value resolution 으로 reference 의 timeSamples 마스킹. **Phase 3**: L2 Discovery 의 R1·R2·R3 모두에서 `read_asset_time_range()` / 폴백 `_stage_local_time_range()` 로 인스턴스의 `asset_start_time/end_time/tps` 자동 채움. **Phase 4**: External Runner 정밀화(speed/pause/resume/restart). LAM Window 에 Sim Speed 슬라이더(Evaluator + External Runner 동시 적용) 와 Pause/Resume/Restart 버튼 추가. `morph.tbs_control_1` 코드 **0줄 변경**, `omni.timeline` **0 import** 유지. |
| v0.9 | 2026-05-10 | **REQ-007 결정 A** — LAM 전용 viewport 자동 생성을 끄고 default viewport 의 `usd_context_name` 만 LAM master 로 마운트하도록 `lam_viewport.py` 재설계(`show()` → 새 창 X, `unmount()` 로 복원). **REQ-008** — `lam_window.py` 가이드형 두 흐름(`① 새로 시작 / ② 기존 합성 USD 열기`) + CollapsableFrame + "Master USD 파일명은 임의" 안내 + `omni.kit.window.filepicker.FilePickerDialog` 적용(USD/Open/Save). LAM Window 가 뜨면 `LamSequenceEditor` 도 같이 자동 오픈. **REQ-009** — `lam_json_test_window.py` 신설(드롭다운 ADD_JSON + DELAY step 백그라운드 스레드 순차 실행, Stop 반응성 100ms 폴링). `extension.toml` 에 `omni.kit.window.filepicker` 의존성 추가. `morph.tbs_control_1` 코드 **0줄 변경**, `omni.timeline` **0 import** 유지. |
| v0.10 | 2026-05-10 | **REQ-007 결정 A → A' 재설정** — 일부 Kit 빌드에서 `viewport.usd_context_name` setter 가 silent fail 해 default viewport·Stage 패널이 LAM 의 prim 을 못 보는 문제 확인. `LAM_MASTER_CONTEXT_NAME = ""` 로 변경(`lam_master_stage.py`)해 LAM 도 default 컨텍스트 사용. `lam_viewport.show()` 는 ctx="" 일 때 mount/폴백 둘 다 no-op (자동 가시). `is_default_visible()` 의미 확장. LAM Window 에 default 컨텍스트 사용 + TBS USD Load 주의 안내 라벨 추가. **REQ-010 신규 채택** — 자산 USD 의 `UsdGeom.GetStageUpAxis` 와 master 의 upAxis 가 다르면 reference prim 에 `RotateX(±90)` 자동 author(`read_asset_up_axis`, `_author_up_axis_fix` in `lam_multi_usd_loader.py`). customData 에 `lam:asset_up_axis`/`lam:master_up_axis` 기록. `morph.tbs_control_1` 코드 **0줄 변경**, `omni.timeline` **0 import** 유지. |
| v0.11 | 2026-05-10 | **REQ-011 신규 채택** — LAM Sequence Editor 가 TBS 시퀀스 편집기와 동일한 4 종 step(`USD_TIMELINE / MOVE / ROTATE / DELAY`) + 동일 UI 필드 + 동일 JSON schema 를 지원. **차이점은 USD_TIMELINE step 한 곳에만** 인스턴스 드롭다운/상태 배지/Re-bind 가 추가되어 step["ref"] 가 4-tuple(REQ-006) 로 박힌다. LAM 측 신규 모듈 6 개(`lam_translate_animation.py` / `lam_rotate_animation.py` / `lam_offset_correction.py` / `lam_hide_helper.py` / `lam_sequence_engine.py` 재구성 / `lam_sequence_editor.py` 전면 재작성). `run_with_previous` 그룹·`step_delay_ms`(initial / follower offset / group-to-group)·`hide_enabled`(refcount + delayed unhide)·USD_TIMELINE `offset_correction_enabled` (TBS 동일 수식 별도 구현)·첫 step `_start_from_current`/`_start_snapshot` 메타(m16→TBS_OFFSET 두 op 분해 author) 모두 LAM 측 별도 구현. Run/Stop 은 Editor background thread. `morph.tbs_control_1` 코드 **0줄 변경**, `morph.tbs_control_1.*` **0 import**, `omni.timeline` **0 import** 유지. |
| v0.12 | 2026-05-11 | **REQ-007 보완 단서(핫픽스 4)** — 사용자 자산의 timeSamples 가 reference 안에 들어 있어 `prim.GetAuthoredAttributes()` 로 잡히지 않고, 잡혀도 default value author 로는 reference timeSamples 를 마스킹할 수 없는 한계 확인(`wrote=0` + `fallback NO timeSamples`). LAM `RuntimeEvaluator` 가 매 frame 마지막에 `omni.timeline.set_current_time(seconds)` 만 호출해 stage 평가를 트리거하도록 정책 보완. master stage 의 `endTimeCode` 자동 확장. attribute reauthor 코드는 호환 보존(no-op). `morph.tbs_control_1` 코드 **0줄 변경**, `morph.tbs_control_1.*` **0 import**, TBS 의 `play()/pause()` 와는 별개 인터페이스 호출. |
| v0.13 | 2026-05-11 | **REQ-007 추가 보완(핫픽스 5)** — 핫픽스 4 의 `omni.timeline.set_current_time` 단일 currentTime 한계 직격: 한 USD_TIMELINE step 만 trigger 했음에도 다른 인스턴스 reference 도 같은 timeCode 로 평가되어 같이 재생되는 문제. `RuntimeEvaluator` 에 비활성 인스턴스 freeze 정책 추가 — `state` 전환 시점에만 reference 의 `Sdf.LayerOffset` 을 author (playing → `(0,1)`, stopped/paused → `(freeze_tc, 0)`). `scale=0` 이라 stage current time 진행과 상관없이 같은 frame 에 머무름. 신규 `_sync_freeze_state` / `_set_prim_layer_offset` 헬퍼. 단일 활성 인스턴스 케이스에서 "두 USD 모두 화면에 보이지만 step 으로 trigger 한 인스턴스만 재생" 의도 충족. 다중 활성 인스턴스 동시 재생은 마지막 set winner 한계 그대로(별도 REQ). `morph.tbs_control_1` 코드 **0줄 변경**, `morph.tbs_control_1.*` **0 import**. |
| v0.14 | 2026-05-11 | **REQ-007 재설계(핫픽스 6) — Per-Instance Layer Offset Mapping**. 핫픽스 5 의 단순 freeze 가 다중 활성 케이스에서 다시 같이 재생되는 한계 발견. 정확한 진단(value resolution / weak layer authoring / UsdSkel evaluation / Hydra evaluation 분리) 후, `RuntimeEvaluator` 를 **wall clock master_seconds + per-instance `Sdf.LayerOffset(offset, scale)` 매핑** 모델로 재설계. `inst_tc(t) = offset_i + master_tc(t) * scale_i`; playing 진입 시 한 번만 author (scale=inst.speed, offset=inst_tc_now − master_tc·speed) → 이후 wall clock 자동 진행으로 자동 sync. stopped/paused → (freeze_inst_tc, 0). loop wrap 감지 시 `_last_mapping_sig` invalidate. `_has_lam_reference` 가드(`lam:instance` customData) 로 LAM 인스턴스만 layerOffset 변경 — TBS 등 다른 자산 평가 영향 0. `_advance_stage_time` 인자: 인스턴스 vt → wall clock master_seconds 로 변경. 모두 stopped 일 때 stage 보존(슬라이더 조작 가능). MOVE/ROTATE/DELAY 는 prim 자체의 TBS_OFFSET XformOp 라 reference layerOffset 과 무관 — 동작 변화 0. `PlaybackScheduler.start()` 가 vt seek 직후 `evaluator.invalidate_mapping(prim_path)` 호출. `morph.tbs_control_1` 코드 **0줄 변경**, `morph.tbs_control_1.*` **0 import**. |
| v0.20 | 2026-05-11 | **REQ-007 보강 (핫픽스 10) — `scale=0` invalid LayerOffset 회피 + WINNER 진단 정확성**. composed metadata 가 `SdfLayerOffset(0, 0)` 인 LAM 1 개로 winner 였음에도 시각적 동시 재생이 발생: USD 의 `SdfLayerOffset.IsValid()` 는 `scale != 0` 만 True 이며 invalid LayerOffset 은 평가 시 unspecified behavior(자동 identity fallback) 라 freeze 가 무시되는 케이스. **`LAM_FREEZE_MIN_SCALE = 1.0e-9`** 클래스 상수 추가, `_set_prim_layer_offset` 진입 시 `abs(scale) < 1e-12` 면 자동 보정. (master_tc 가 1e9 sec 진행해도 inst_tc 는 1 sec → 시각적 freeze 동등.) + WINNER 진단 로직 개선: stack[0] 만 보던 것을 `lam_idx vs master_idx` 비교로 변경(stack[0] 이 session anonymous spec 이라도 LAM 이 master USD 보다 위면 winner 로 정확히 판정). 진단 출력에 `lam_idx / master_idx / is_lam_refs_winner` 표기. `morph.tbs_control_1` 코드 **0줄 변경**, 사용자 master USD PrimSpec 변경 **0**. |
| v0.19 | 2026-05-11 | **REQ-007 보강 (핫픽스 9) — Specifier.Def + Pure explicit override + Instanceable 차단 + 강화 진단**. session sublayer attach 후에도 weaker reference compose 가 살아남거나 Hydra prototype sharing 으로 LayerOffset 이 무력화되는 케이스 차단. (1) `sub_spec.specifier = Sdf.SpecifierOver` → `Sdf.SpecifierDef` (강한 prim 정의로 weaker compose 차단). (2) `referenceList/payloadList` 의 `ClearEdits/SetItems/explicitItems/append` 3 단계 fallback chain 제거 → `explicitItems = [new]` 직접 대입 1 줄만 사용 (ListOp merge semantics 완전 회피). (3) author 후 `prim.SetInstanceable(False)` 명시적 호출 (Hydra/usdImaging prototype sharing 차단). (4) `prim.GetMetadata("references"/"payload")` 의 raw ListOp 출력. (5) post-attach stack 진단에 `ref[source] asset=... prim=... offset=... scale=...` 상세 표기. (6) `TOP WINNER LAYER prim=... layer=... is_lam=True/False` 진단 + WARN. `morph.tbs_control_1` 코드 **0줄 변경**, 사용자 master USD PrimSpec 변경 **0**. |
| v0.18 | 2026-05-11 | **REQ-007 보강 (핫픽스 8) — Session Layer Sublayer Attach (Opt-2 정통)**. 핫픽스 7 의 root.subLayerPaths.insert(0, ...) 가 USD layer 강도 규칙(`Session > Root > Root.subLayers`) 을 잘못 가정해 **LAM sublayer 가 root layer (master_1.usd) 보다 weaker 가 되어** master USD 의 reference 가 winner 로 평가되던 문제(post-attach stack 진단으로 확인: `stack[1]=master_1.usd` 가 `stack[2][LAM]=lam_inst_aaa` 보다 위). **session layer 의 subLayerPaths 에 등록**하도록 변경: session layer 자체가 root 보다 stronger 이므로 session 의 sublayer 들도 root 보다 strong → USD ListOp explicit override 가 무조건 winner. `MasterStage._pick_attach_layer(stage)` 신규 helper(1순위 `GetSessionLayer()`, fallback `GetRootLayer()`). `ensure_inst_sublayer` 진단 로그에 `into=session(...)` / `into=root(...)` 표기. `remove_inst_sublayer` 는 session 과 root 양쪽 모두 cleanup 시도. + `_set_prim_layer_offset` 의 `composed_refs=-2` 원인 수정(`Usd.Prim.GetReferences()` 는 ListEditorProxy 가 아님 → `prim.GetMetadata("references")` 의 ListOp 직접 카운트). `morph.tbs_control_1` 코드 **0줄 변경**. |
| v0.17 | 2026-05-11 | **REQ-007 재설계 (핫픽스 7) — Per-Instance Anonymous Sublayer Override (Opt-1)**. 핫픽스 6.x 의 `Sdf.PrimSpec.referenceList` 직접 조작 방식이 (a) 사용자 master USD 의 PrimSpec 을 직접 수정하여 save 시 사용자 파일 오염 위험, (b) `Sdf.ChangeBlock` 없는 변경이 Hydra ChangeNotice 를 보장 못 해 시각적 무반영 가능성, (c) cross-layer override 가 일부 USD 빌드/케이스에서 winner 로 평가되지 않는 한계를 가진다는 진단(`prepended=0 explicit=1 appended=0` 로그가 정확함에도 시각적 동시 재생) 후 폐기 결정. **`MasterStage` 가 인스턴스마다 1개의 anonymous `Sdf.Layer.CreateAnonymous("lam_inst_<usd_id>")` 를 만들고 root layer 의 `subLayerPaths.insert(0, layer.identifier)` 로 가장 strong 슬롯에 삽입**. `RuntimeEvaluator._set_prim_layer_offset` 는 사용자 master USD 의 PrimSpec 을 절대 만지지 않고, 자기 sublayer 안에서 `with Sdf.ChangeBlock():` 으로 묶어 `over "/.../prim" { references = [@asset@</prim_path_in_asset>(LayerOffset(o,s))] }` 1 개를 explicit set. master USD 의 reference / payload / sublayer / variant 어떤 composition arc 든 LAM sublayer 가 stronger 라 무조건 winner. 진단 로그 강화: `[LAM/L1a] sublayer attached`, `[LAM/L5] sublayer mapping authored ... composed_refs=N composed_pays=N`, `post-attach stack[0][LAM] ...` 로 winner 검증. `lam_multi_usd_loader.remove_usd` 가 `master.remove_inst_sublayer(prim_path)` 도 호출 (sublayer 누수 0). `lam_window._invalidate_attr_caches` 가 `evaluator.invalidate_mapping(None)` 까지 호출. `morph.tbs_control_1` 코드 **0줄 변경**, 사용자 master USD 의 PrimSpec **변경 0**. |
| v0.16 | 2026-05-11 | **REQ-007 보강(핫픽스 6.3)** — (a) `_set_prim_layer_offset` 에서 USD `SdfListOp` 의 explicit mode 강제를 3 단계 fallback 으로 변경: 1차 `ClearEdits()` + `SetItems(new_refs)` (정통), 2차 `explicitItems = new_refs`, 3차 슬라이스 + `append`. 이전엔 `[:] = []` + `append` 만 사용해 mode 가 explicit 으로 전환되지 않고 prepended 에만 author 되는 케이스가 있어 cross-layer 의 weaker layer references 가 무효화되지 않아 두 USD 가 같이 재생되는 문제. + 첫 author 직후 1 회 진단 로그(`prepended=N explicit=N appended=N` 카운트). (b) master stage 의 `timeCodesPerSecond` / `framesPerSecond` 를 `LAM_FIXED_FPS=30` 으로 강제 author (`_ensure_stage_fps_lam_fixed`, 1 회) — omni.timeline 슬라이더가 60fps 등 다른 값으로 표시되는 문제 해결. `morph.tbs_control_1` 코드 **0줄 변경**. |
| v0.15 | 2026-05-11 | **REQ-007 보강(핫픽스 6.1 + 6.2)** — (6.1) `_has_lam_reference` 가드를 호출 경로에서 제거 — `customData('lam:instance')` 가 USD save/load 사이 형식 차이로 False 를 반환해 mapping 자체가 적용되지 않는 문제. evaluator 가 순회하는 인스턴스는 이미 `registry.all_instances()` 로 LAM 만이라 가드는 over-protection. + 인스턴스마다 첫 호출 시 1 회 진단 로그 추가. (6.2) `_set_prim_layer_offset` 를 `Sdf.PrimSpec.referenceList` 직접 조작으로 변경 — `Usd.References.ClearReferences()` 가 EditTarget layer 의 `prepended` 만 비울 뿐 root layer (`Open Master USD` 로 직접 load) 에 explicit author 된 reference 를 무효화하지 못해 freeze 효과가 시각적으로 없던 문제. 모든 ListOp 항목(prepended/appended/explicit/ordered) 수집 + 새 LayerOffset 으로 clone + 기존 ListOp 모두 비우고 `explicitItems` 로 set → cross-layer weaker layer references 까지 모두 무효화 → mapping 이 항상 winner. + `master_seconds` 누적을 `any_playing_now` 인 frame 만으로 변경(stopped 일 때 wall clock 정지 → 다음 RUN 의 master_tc 가 0 부터 시작). + `LAM_FIXED_FPS = 30.0` 으로 evaluator 의 시각 변환 fps 30 고정(inst.asset_tps / master stage tps 무시, master stage `timeCodesPerSecond` 는 변경하지 않아 TBS 영향 0). `morph.tbs_control_1` 코드 **0줄 변경**. |

---

## 11. 도입 단계 템플릿(요구사항 확정 시 적용)

요구사항이 합의되어 코드 수정에 들어갈 때 본 템플릿을 복사해 카드별로 채운다.

- **Phase 0**: 신규 모듈/위젯 단독 추가(어디서도 import 안 됨 → 회귀 0)
- **Phase 1**: 기존 호출 경로에 분기 1 곳만 추가(예: 시퀀스 새 step type)
- **Phase 2**: 사용자 진입점 UI 추가(별도 섹션, 기존 위젯 변경 0)
- **Phase 3**: 분할 화면/teardown hook 통합
- **Phase 4**: 사용성 개선(자산 라이브러리, 드롭다운 등)

각 Phase 종료 조건: **기존 시뮬 시작 → 정상 진행 → 종료** 회귀 통과 + 본 문서 §10 에 항목 추가.

---

## 12. 부록 — 절대 보호 영역(체크리스트)

본 시스템 수정 시 아래는 손대지 않거나, 손대더라도 회귀 테스트 필수.

- [ ] `usd_animation_control._states[k]` 의 4 종 sub 라이프사이클(특히 `_play_token`, `_end_fix_sub`)
- [ ] `usd_animation_control` 공개 함수의 시그니처(인자 추가는 default 인자만, 위치 인자 의미는 불변)
- [ ] `sequence_engine._start_step` 의 `USD_TIMELINE` 분기 — 새 분기를 추가하더라도 본 분기는 그대로 둔다
- [ ] `sequence_engine._step_duration_sec` 의 `USD_TIMELINE` 추정식(시퀀스 스케줄 정확도에 직결)
- [ ] 분할 화면 보조 컨텍스트 이름 규칙(`morph_tbs_split_aux_N`) — 다른 모듈도 이 키로 라우팅함
- [ ] timeline event stream 추가 구독 금지(중복 완료 위험) — 신규 평가기는 `update_event_stream` 사용
- [ ] **LAM 측 (`morph.lam_control`) 은 `morph.tbs_control_1` 의 어떤 모듈도 import 하지 않는다** — REQ-002 0줄 변경 원칙 보장
- [ ] **LAM 측은 `omni.timeline.set_current_time()` 을 호출하지 않는다** — §3.1 단일 stage 멀티 평가 한계로 인스턴스 간 평가 시각이 섞이지 않게 유지(REQ-004)
- [ ] **LAM 시퀀스 step 의 `ref` 는 단일 키가 아니라 4-튜플로 저장** — `prim_path / guid / instance_id / source_asset` 모두 보존(REQ-006)
- [ ] **LAM 측은 default 컨텍스트(`""`) 를 사용한다(REQ-007 결정 A')** — 별도 LAM 컨텍스트(`morph_lam_master`) 를 만들지 않는다. Kit 의 모든 기본 패널(Viewport/Stage/Property) 이 자동으로 LAM 의 prim 을 보도록 한다. 별도 LAM Viewport 창은 [강제 열기] 사용자 액션 시에만 생성.
- [ ] **LAM 측은 자산 reference attach 직후 upAxis 를 비교해 다르면 RotateX(±90) 보정을 author 한다(REQ-010)** — Y-up 자산이 Z-up master 위에 눕혀 보이는 문제 방지. 보정 op 의 `opSuffix` 는 `lamUpAxisFix` 하나로 고정(중복 author 금지).
- [ ] **LAM 측은 `_set_prim_layer_offset` 의 author 위치가 사용자 master USD 의 PrimSpec 이 되어서는 안 된다(REQ-007 핫픽스 7)** — 인스턴스마다 1개의 anonymous sublayer (`lam_inst_<id>`) 를 만들어 그 안에서 author 해야 한다. 사용자 master USD 의 PrimSpec 을 직접 수정하면 save 시 사용자 파일 오염 + Hydra cache invalidate 누락 위험. `Sdf.ChangeBlock` 으로 묶어 한 번의 ChangeNotice 만 발생시킬 것.
- [ ] **LAM 측 인스턴스 sublayer 는 반드시 `stage.GetSessionLayer()` 의 `subLayerPaths` 에 attach 해야 한다(REQ-007 핫픽스 8)** — root layer 의 subLayerPaths 에 끼우면 USD 의 layer 강도 규칙상(`Session > Root > Root.subLayers`) root 자체보다 weaker 가 되어 master USD 의 reference 가 winner 로 평가되어 LayerOffset 이 무시된다. session 미가용 환경에서만 root 로 fallback 하고, 그 경우 동작 보장이 약함을 인지해야 한다.
