# TBS Control 2 — Dock 2분할 분할선 가드 (SplitterGuard) 작업 기록

> **상태**: **미해결 (UNRESOLVED)** — 분할선 드래그 차단은 동작하나, 좌·우 미세 오버레이 띠 완전 제거 실패  
> **작성일**: 2026-07-08  
> **중단 사유**: `ui.Window` 최소 칠해짐 폭·위치 튜닝만으로는 뷰포트 침범 없이 완전 비가시 달성 한계  
> **코드 기준**: `sim_multi_view.py` (SplitterGuard ~L489–765), `sim_control_defaults.py`  
> **Git 원복 기준선**: `09b0054` (`2분할화면 크기조절 막기`) — 드래그 차단이 **처음 동작**했던 커밋

---

## ① 한 줄 요약

**Dock 2분할** (`USE_VIEWPORT_WIDGET_SPLIT=False`)에서 **분할선 드래그로 50:50 비율이 바뀌지 않게** 하려고, Dock 분할선 위에 **투명 `ui.Window` 오버레이**(`TBS_SimSplit_SplitterGuard`)로 입력을 선점한다.  
**드래그 차단은 달성**했으나, Kit가 `width=1` 요청에도 **실제로 더 넓게 그리는** 특성 때문에 **좌·우 1px 전후의 희미한 띠**가 남는다. 이 문서는 이후 재개 시 참고용이다.

---

## ② 목표 vs 현재 결과

| 항목 | 목표 | 현재 (2026-07-08 중단 시점) |
|------|------|---------------------------|
| 분할선 드래그 차단 | Dock splitter 드래그 불가 | **달성** — 파란 하이라이트·비율 변경 안 됨 |
| 뷰포트 조작 | 클릭·orbit·zoom 정상 | **달성** (가드 영역 밖) |
| 오버레이 비가시 | 분할선 히트존만 덮고 **안 보임** | **미달** — 이전(15~20px 띠)보다 **크게 줄었으나** 좌·우 미세 띠 잔존 |
| 비율 되돌리기 | 드래그 후 `dock_in` 스냅백 | **의도적으로 미구현** (사용자 거부) |

---

## ③ 적용 조건 (설정)

| 설정 | 파일 | 기본값 | 의미 |
|------|------|--------|------|
| `USE_VIEWPORT_WIDGET_SPLIT` | `sim_control_defaults.py` | `False` | **Dock 2분할** 경로. `True`면 Widget 분할 — 본 가드 **미적용** |
| `LOCK_VIEWPORT_SPLIT_USER_RESIZE` | `sim_control_defaults.py` | `True` | 타일 floating 리사이즈·이동 제한 + 분할선 가드 on |
| `TBS_SIM_VIEWPORT_SPLIT_LOCK_RESIZE` | 환경변수 | (미설정 시 on) | `0` / `false` 등이면 잠금 off |

`apply_viewport_split_user_resize_lock()` 은 `is_split_widget_layout_active(ext)` 이면 **즉시 return** (Widget 분할과 배타).

---

## ④ 구현 방식 (현재 채택 구조)

### 4.1 전체 흐름

```
apply_viewport_split_user_resize_lock(ext)
  ├─ 각 타일 Workspace 창: NO_RESIZE | NO_MOVE 플래그
  └─ _install_viewport_split_splitter_guard(ext)
        ├─ ui.Window("TBS_SimSplit_SplitterGuard") 1회 생성
        ├─ post_update 구독 → 매 프레임 _tick_viewport_split_splitter_guard
        └─ _compute_viewport_split_splitter_rect → _apply_splitter_guard_geometry
```

### 4.2 드래그 차단 메커니즘 (동작 확인됨)

1. **`ui.Window`** + `NO_TITLE_BAR` / `NO_BACKGROUND` 등 플래그
2. **`win.set_top_modal()`** — 매 프레임 geometry 적용 시 호출
3. **`set_mouse_pressed_fn` / `set_mouse_released_fn`** → `return True` (이벤트 consume)
4. 내용: **투명 `Frame` + `Spacer`** (`background_color: 0`, `border_width: 0`)

### 4.3 geometry 계산 (`_compute_viewport_split_splitter_rect`)

**입력**: Workspace `Viewport` 왼쪽 타일, `_split_window_name(1)` 오른쪽 타일의 `position_x/y`, `width/height`.

| 축 | 계산 |
|----|------|
| **가로 anchor** | `gap_l = lx + lw`, `gap_r = rx`. 접합·갭·겹침에 따라 `anchor` = `gap_l` 또는 `gap_r` |
| **가로 gw** | `dock_gap ≥ 1` → `min(dock_gap, HIT_PX)`; 겹침 → `min(overlap, HIT_PX)`; 접합 → `HIT_PX` |
| **가로 gx** | `anchor - ORIGIN_LEFT` (Kit 칠해짐 우측 끝을 접합선에 맞추기 위한 **경험적** 오프셋) |
| **세로** | 두 타일 **교집합** (`max(ly,ry)` ~ `min(ly+lh, ry+rh)`) — 원본 합집합 대비 띠 축소에 기여 |

### 4.4 가시성 관련 보조 (현재 코드)

| 처리 | 목적 |
|------|------|
| `_hide_splitter_guard_workspace_duplicate()` | 동명 `Workspace.get_window` 에 geometry **이중 적용** 시 넓은 띠 — **숨김만**, geometry는 `ext._tbs_split_splitter_guard_win` 만 갱신 |
| `Rectangle` → `Frame`+`Spacer` | `0x01000000` 미세 알파 띠 제거 |
| 매 프레임 `win.flags` 재적용 | 포커스·배경 플래그 유지 |

### 4.5 현재 튜닝 상수 (`sim_multi_view.py`)

```python
_SPLITTER_GUARD_HIT_PX = 1          # 논리 width (gw)
_SPLITTER_GUARD_CHROME_SLOP = 2/3   # 참고용; gx 공식에는 미사용
_SPLITTER_GUARD_ORIGIN_LEFT = 8.0   # anchor에서 왼쪽으로 뺀 창 원점 (px)
```

**중요**: `gw=1` ≠ 화면 1px. Kit `ui.Window`는 **최소 ~8px 전후**로 칠해질 수 있어, `ORIGIN_LEFT`로 **칠해진 우측 끝**을 `gap_l`에 맞추는 방식으로 수동 튜닝했다. `0.5` / `0.3` px `gw`는 **효과 없음** (정수·최소 폭 클램프).

---

## ⑤ 주요 심볼·파일

| 심볼 | 역할 |
|------|------|
| `apply_viewport_split_user_resize_lock` | 진입점 — 타일 플래그 + 가드 설치 |
| `teardown_viewport_split_resize_lock` | 구독 해제·창 숨김 |
| `_install_viewport_split_splitter_guard` | post_update 구독 등록 |
| `_tick_viewport_split_splitter_guard` | 매 프레임 rect 재계산·적용 |
| `_compute_viewport_split_splitter_rect` | **geometry 핵심** — 이후 튜닝도 여기·상수 위주 |
| `_apply_splitter_guard_geometry` | `ext` 소유 `ui.Window` 만 position/size/visible |
| `_ensure_splitter_guard_window` | 최초 `ui.Window` 생성 |
| `ext._tbs_split_splitter_guard_win` | 가드 창 핸들 |
| `ext._tbs_split_splitter_guard_sub` | post_update 구독 |
| `ext._tbs_split_used_dock_layout` | Dock 레이아웃일 때만 가드 활성 |

**호출 위치** (대표): 분할 적용·레이아웃 복원 시 `sim_multi_view.py` 내 다수 (grep `apply_viewport_split_user_resize_lock`).

---

## ⑥ 시도 이력 — 하지 말아야 할 것

| 시도 | 결과 | 비고 |
|------|------|------|
| 원본 geometry (`mid` 중심, `gw=max(10,gap+10)`, 세로 합집합) | 드래그 차단 OK, **좌·우 15~20px 띠** | `09b0054` 기준선 |
| rect만 축소·교집합 (구조 유지) | 띠 감소, 드래그 유지 | **올바른 방향** |
| `carb.input` only, `ui.Window` 제거 | 띠는 줄 수 있으나 **드래그 차단 실패** | 재시도 금지 |
| `carb.input` + `ui.Window` 혼합 + Workspace 분리 등 대규모 변경 | **드래그 풀림**·띠 악화 | 구조 갈아엎기 금지 |
| 매 프레임 `dock_in` 비율 되돌리기 | (미검증) 사용자 **명시 거부** | |
| `LOCK=False` 로 잠금 해제 | 드래그 가능 — 목표 아님 | |
| `gw` 0.5 / 0.3 | **화면 띠 폭 변화 없음** | Kit 최소 칠해짐 한계 |

**교훈**: 드래그 차단이 되는 **`ui.Window` + modal + mouse consume** 을 유지한 채, **geometry·투명도·Workspace 이중 적용** 만 최소 수정할 것.

---

## ⑦ 미해결 — 잔존 증상 (2026-07-08)

- 분할선 **좌·우로 1px 전후** 희미한 세로 띠가 남음 (해상도·DPI·창 크기에 따라 가시성 변동 가능).
- `ORIGIN_LEFT` 를 올리면 우측은 줄고 좌측이 늘고, 내리면 반대 — **완전 제거 포인트를 찾지 못함**.
- 스크린샷 상 가끔 `364x664` 등 **창 크기 힌트**가 보인 적 있음 (가드 창 hover/디버그 여부는 추가 확인 필요).

---

## ⑧ 이후 작업 시 제안 (미착수)

1. **Kit API 조사**: `ui.Window` 없이 **입력만** 가로채는 공식/내부 API 존재 여부 (extension examples, ImGui `InvisibleButton` 패턴).
2. **실측**: 런타임에 가드 창 `position_x/width` vs 실제 hit-test rect 로그 (1회성 diag 플래그).
3. **`ORIGIN_LEFT` 자동 보정**: `gap_l`/`gap_r`·DPI·`dock_gap` 에 따른 lookup table (수동 1px 튜닝 대체).
4. **Dock splitter 비활성화**: 분할선 자체를 Kit Dock 설정으로 lock 할 수 있는지 (`dock_in` 스냅백은 제외).
5. **대안 레이아웃**: `USE_VIEWPORT_WIDGET_SPLIT=True` 전환 시 본 이슈 회피 가능 — 별도 과제 (`tbs_control_2_viewport_widget_split_*.md` 참고).
6. **가드 색을 뷰포트 배경과 동일**한 불투명색으로 **갭 영역만** 덮기 (투명 대신 위장) — 미관 타협안.

---

## ⑨ 검증 체크리스트 (재개 시)

- [ ] Kit **완전 재시작** 후 테스트 (hot reload만으로는 geometry 캐시 이슈 가능)
- [ ] 분할선 hover 시 **파란 splitter 하이라이트 없음**
- [ ] 분할선 **드래그로 좌우 비율 변경 불가**
- [ ] 좌·우 뷰포트 **orbit / pan / zoom 정상**
- [ ] 좌·우 **띠 없음** (목표 — 현재 미달)
- [ ] `LOCK_VIEWPORT_SPLIT_USER_RESIZE=False` 시 가드 숨김·드래그 복구
- [ ] Console / Content 등 **주변 Dock 레이아웃** 깨짐 없음

---

## ⑩ 추가 미반영 사항 (추후 이 섹션에 누적)

> 사용자 요청: 다른 수정·미완료 항목도 **이 문서에 반영** 예정. 아래에 항목을 추가한다.

| # | 주제 | 상태 | 메모 |
|---|------|------|------|
| — | *(아직 없음)* | — | 향후 항목 추가 |

### 관련 문서 (별도 트랙)

| 문서 | 관련 내용 |
|------|-----------|
| `tbs_control_2_viewport_coupling_investigation_ko.md` | Widget 분할 P0 카메라 coupling |
| `tbs_control_2_viewport_widget_split_*.md` | Widget 2분할 전환·상태 |
| `tbs_control_2_multi_split_requirements_ko.md` | 다분할 요구사항 |

---

## ⑪ 변경 이력 (본 이슈)

| 일자 | 요약 |
|------|------|
| (커밋 `09b0054`) | 최초 `TBS_SimSplit_SplitterGuard` — 드래그 차단 OK, 넓은 띠 |
| 2026-07-07~08 | geometry 축소(교집합·갭 정렬), `ORIGIN_LEFT` 수동 튜닝, Workspace 이중 적용 제거, 완전 투명 Frame |
| 2026-07-08 | **작업 중단** — 드래그 차단 유지, 좌·우 미세 띠 잔존. 본 문서 작성 |

---

*이 문서는 “완료”가 아닌 **진행 중단·미해결** 기록이다. 재개 시 §⑧·§⑨부터 진행할 것.*
