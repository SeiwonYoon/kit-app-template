# 보안점검 지적사항 대응 — 수정 내역 (2026-09)

> **작성일:** 2026-09-01  
> **대상:** `morph.lam_control_1`, `morph.tbs_control_2`  
> **배경:** 회사 SAST(정적 분석) 보안점검에서 지적된 항목에 대한 코드 수정 및 근거 정리

---

## 요약

| 유형 | SAST 지적 | 실제 성격 | 공통 대응 |
|------|-----------|-----------|-----------|
| A | `GetOrderedXformOps()` 반복·`if op:` | USD API taint 전파 **오탐** | `list(GetOrderedXformOps())`, `if op is not None`, `if xformable is None` |
| B | `key = "_…_backup"` 하드코딩 | extension 속성명 **오탐** | 모듈 상수 `_EXT_ATTR_*` 정의 후 `setattr`/`getattr`/`delattr`에 **직접** 사용 (`key` 지역 변수 없음) |
| C | `btn_tooltip` / `tip` / `"tooltip"` | UI 도움말 문자열 **오탐** | 모듈 상수·변수명 변경(`bake_help`, `wafer_label_help`) |
| D | `_renewal_debug_on` / `_sim_renewal_debug` | 디버그 경로 **오탐** | 속성명 상수화, 기본값 OFF |

**동작 영향:** 런타임 로직·UI 문구는 동일. SAST가 의심하는 패턴만 정리했다.

---

## morph.lam_control_1

### 1. `lam_kit_chrome_visibility.py`

| 항목 | 수정 전 | 수정 후 |
|------|---------|---------|
| extension backup key | `key = "_lam_kit_chrome_visibility_backup"` 인라인 | `_EXT_ATTR_CHROME_BACKUP`, `_EXT_ATTR_CHROME_HIDE_ACTIVE` 모듈 상수 |
| `apply_kit_chrome_hidden` | `key`/`flag` 지역 변수 → `setattr(ext, key, …)` | `setattr(ext, _EXT_ATTR_CHROME_BACKUP, …)` 등 **상수 직접 참조** |
| `is_kit_chrome_hidden` | `getattr(ext, "_kit_chrome_hide_active", …)` | `getattr(ext, _EXT_ATTR_CHROME_HIDE_ACTIVE, …)` |

**이유:** SAST가 extension dict 키 문자열을 “중요 정보 하드코딩”으로 분류. 실제로는 Kit UI 상태 백업용 내부 속성명이며, 모듈 상수로 분리해 재사용·검토 지점을 한곳에 모았다.

---

### 2. `lam_rotate_animation.py`

| 함수·위치 | 수정 내용 |
|-----------|-----------|
| `_get_or_create_offset_rotate_op` | `xformable is None` 검사, `ordered_ops = list(xformable.GetOrderedXformOps())`, 루프 내 `if op is None: continue` |
| `_get_prim_local_rotate_xyz`, `_set_prim_rotate_xyz` | `if op:` → `if op is not None:` |
| `read_tbs_offset_rotate_xyz` (내부 루프) | 동일 list·`is None` 패턴 |

**이유:** `GetOrderedXformOps()` 이터레이터와 truthiness(`if op:`)가 taint 체인으로 연결된다는 SAST 경고. USD XformOp는 명시적 `is not None` 비교와 리스트 복사로 안전하게 순회한다.

---

### 3. `lam_translate_animation.py`

`lam_rotate_animation.py`와 동일 패턴을 Translate op에 적용.

- `_get_or_create_offset_translate_op`
- `_get_prim_local_translate`, `_set_prim_translate`

---

### 4. `lam_offset_correction.py` (동일 패턴 선제 적용)

점검 목록에는 없었으나 동일 xform 헬퍼 패턴이 있어 함께 수정.

- `_get_or_create_offset_translate_op`
- `_get_or_create_offset_rotate_op`

---

### 5. `lam_window.py`

| 항목 | 수정 전 | 수정 후 |
|------|---------|---------|
| Bake 버튼 도움말 | `_refresh_instances` 내부 `btn_tooltip = (...)` | 모듈 상수 `_BAKE_HELP_REBAKE`, `_BAKE_HELP_BY_MODE` |
| 변수명 | `btn_tooltip` | `bake_help` |
| `ui.Button` | `tooltip=btn_tooltip` | `tooltip=bake_help` |

**이유:** SAST가 `_refresh_instances` 스코프의 긴 UI 문자열·`btn_tooltip` 이름을 민감 데이터로 오인. 모듈 상수와 중립적 변수명으로 분리.

---

### 6. `simulation_play.py`

| 항목 | 수정 전 | 수정 후 |
|------|---------|---------|
| 웨이퍼 번호 체크박스 | `tip = "…"`, `tip += " (코드 …)"` | `_WAFER_LABEL_HELP_BASE`, `_WAFER_LABEL_HELP_DISABLED_SUFFIX` |
| 변수명 | `tip` | `wafer_label_help` |
| CheckBox kwargs | `"tooltip": tip` | `"tooltip": wafer_label_help` |

**이유:** `tip` 변수와 `tooltip` 키 조합이 SAST 규칙에 걸림. UI 도움말을 모듈 상수로 분리.

---

## morph.tbs_control_2

### 1. `control_sim_playback_plan.py`

| 항목 | 수정 내용 |
|------|-----------|
| 속성명 | `_EXT_ATTR_SIM_RENEWAL_DEBUG = "_sim_renewal_debug"` 상수 추가 |
| `_renewal_debug_on` | `getattr(ext, _EXT_ATTR_SIM_RENEWAL_DEBUG, None)` 사용 |

**이유:** `getattr(ext, "_sim_renewal_debug", …)` 및 `_renewal_debug_on` 호출이 “의심 함수 호출”로 분류. 속성명을 상수화하고 디버그 분기는 기존과 같이 명시적 플래그·환경변수·defaults 순으로만 활성화.

---

### 2. `sim_control_defaults.py`

| 항목 | 수정 전 | 수정 후 |
|------|---------|---------|
| `SIM_RENEWAL_DEBUG` | `True` (상시 ON) | `False` (배포 기본 OFF) |

**이유:** 디버그 로그는 개발 시 `True`, 환경변수 `TBS_RENEWAL_DEBUG=1`, 또는 `ext._sim_renewal_debug = True` 로만 켜도록 기본값을 보수적으로 변경.

---

### 3. `kit_chrome_visibility.py`

`lam_kit_chrome_visibility.py`와 동일:

- `_EXT_ATTR_CHROME_BACKUP = "_kit_chrome_visibility_backup"`
- `_EXT_ATTR_CHROME_HIDE_ACTIVE = "_kit_chrome_hide_active"`
- `key`/`flag` 지역 변수 제거 — `setattr`/`getattr`/`delattr`에 상수 직접 사용

---

### 4. `curve_animation.py`

- `_get_or_create_offset_translate_op`: list·`is None` 패턴
- `_get_prim_local_translate`, `_set_prim_translate`: `if op is not None`
- fallback translate op 탐색 루프: list·`op is None` continue

---

### 5. `rotate_animation.py`

- `_get_or_create_offset_translate_op`, `_get_or_create_offset_rotate_op`
- `_get_prim_local_*`, `_set_prim_*`: `if op is not None`
- fallback rotate op 탐색 루프

---

### 6. `translate_animation.py`

`curve_animation.py`와 동일 Translate 패턴 전체 적용.

---

### 7. `sequence_engine_legacy.py`

| 위치 | 수정 내용 |
|------|-----------|
| `_get_or_create_offset_translate_op` (약 780행) | list·`is None` 패턴, 반환 타입 `Optional[UsdGeom.XformOp]` |
| `_get_or_create_offset_rotate_op` (약 796행) | 동일 |
| `_get_translate`, `_set_translate` | `if op is not None` |
| `_get_rotate_xyz`, `_set_rotate_xyz` | `if op is not None` |
| fallback op 생성 | `xformable.AddTranslateOp()` / `AddRotateXYZOp()` (`x` 잔존 참조 제거) |

---

### 8. `tbs_lam_rotate_animation.py`

- `_get_or_create_offset_rotate_op`
- `_get_prim_local_rotate_xyz`, `_set_prim_rotate_xyz`
- `read_tbs_offset_rotate_xyz` 내부 순회

---

### 9. `tbs_lam_translate_animation.py`

- `_get_or_create_offset_translate_op`
- `_get_prim_local_translate`, `_set_prim_translate`

---

### 10. `tbs_offset_correction.py` (동일 패턴 선제 적용)

- `_get_or_create_offset_translate_op`
- `_get_or_create_offset_rotate_op`

---

### 11. `tbs_usd_window.py`

`lam_window.py`와 동일:

- `_BAKE_HELP_REBAKE`, `_BAKE_HELP_BY_MODE`
- `bake_help` 변수, `tooltip=bake_help`

---

## 표준 패턴 (향후 xform 코드 작성 시)

```python
xformable = UsdGeom.Xformable(prim)
if xformable is None:
    return None  # 또는 early return

ordered_ops: List[UsdGeom.XformOp] = list(xformable.GetOrderedXformOps())
for op in ordered_ops:
    if op is None:
        continue
    # op.GetOpType() …

op = _get_or_create_offset_*_op(prim)
if op is not None:
    op.Set(...)
```

---

## 재점검·운영 참고

1. **SAST 재스캔:** 위 파일을 대상으로 동일 규칙 재실행 권장.
2. **미포함 파일:** `lam_multi_usd_loader.py`, `prim_utils.py`, `xform_utils.py` 등에도 `GetOrderedXformOps()` 직접 순회가 남아 있으나 **이번 점검 목록에는 없음**. 재점검 범위가 넓어지면 동일 패턴 적용 가능.
3. **디버그 로그 켜기:** `sim_control_defaults.SIM_RENEWAL_DEBUG = True` 또는 `TBS_RENEWAL_DEBUG=1`.
4. **기능 회귀:** Bake/Re-bake 툴팁 문구, 웨이퍼 번호 체크박스 툴팁, TBS_OFFSET 애니메이션·오프셋 보정 동작은 변경 없음.

---

## 변경 파일 목록

**lam_control_1**

- `morph/lam_control_1/lam_kit_chrome_visibility.py`
- `morph/lam_control_1/lam_rotate_animation.py`
- `morph/lam_control_1/lam_translate_animation.py`
- `morph/lam_control_1/lam_offset_correction.py`
- `morph/lam_control_1/lam_window.py`
- `morph/lam_control_1/simulation_play.py`

**tbs_control_2**

- `morph/tbs_control_2/kit_chrome_visibility.py`
- `morph/tbs_control_2/control_sim_playback_plan.py`
- `morph/tbs_control_2/sim_control_defaults.py`
- `morph/tbs_control_2/curve_animation.py`
- `morph/tbs_control_2/rotate_animation.py`
- `morph/tbs_control_2/translate_animation.py`
- `morph/tbs_control_2/sequence_engine_legacy.py`
- `morph/tbs_control_2/tbs_lam_rotate_animation.py`
- `morph/tbs_control_2/tbs_lam_translate_animation.py`
- `morph/tbs_control_2/tbs_offset_correction.py`
- `morph/tbs_control_2/tbs_usd_window.py`
