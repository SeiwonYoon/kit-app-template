# morph.pick_filter Extension

## Overview

`morph.pick_filter`는 NVIDIA Omniverse Kit 기반 애플리케이션에서

- Prim의 Pick 가능 여부 제어
- Selection 제어
- Viewport Selection 비활성화
- Frame(Focus)
- Temperature 메타데이터 관리
- ✅ **Leaf Name(Prim 이름) 목록 기반 Selection / Selection 제거 / Pickable 일괄 적용**

기능을 제공하는 **서비스형 익스텐션**입니다.

외부 익스텐션 또는 Web UI는 `PickFilterService`를 통해 기능을 호출하도록 설계되어 있습니다.

> 중요:
> - 서비스는 **그룹/정책 정의 데이터를 보유하지 않습니다.**
> - 외부(예: Dummy UI/Web UI)는 “leaf name 목록”만 전달하고,
>   서비스가 캐시 기반으로 name→path resolve 후 동작을 수행합니다.

---

# Service Entry Point

## ensure_service()

```python
ensure_service() -> PickFilterService
```

서비스 인스턴스를 안전하게 생성 및 시작합니다.
이미 존재하는 경우 기존 인스턴스를 반환합니다.

---

# Lifecycle API

## start()

```python
start() -> None
```

서비스 시작 (중복 호출 안전)

## stop()

```python
stop() -> None
```

서비스 종료 및 viewport 상태 복구

---

# Cache API

## get_revision()

```python
get_revision() -> int
```

현재 캐시 revision 반환

## get_items_cached()

```python
get_items_cached() -> List[Dict[str, Any]]
```

현재 캐시된 prim 목록 반환

## refresh_cache()

```python
refresh_cache() -> List[Dict[str, Any]]
```

스테이지 재스캔 후 캐시 갱신

---

# Pickable API

## set_pickable()

```python
set_pickable(path: str, pickable: bool, include_descendants: bool = False)
```

특정 prim의 pick 가능 여부 설정

## set_pickable_bulk()

```python
set_pickable_bulk(paths: List[str], pickable: bool)
```

여러 prim 일괄 적용

## lock_all()

```python
lock_all()
```

전체 pick 비활성화

## unlock_all()

```python
unlock_all()
```

전체 pick 활성화

---

# Temperature API

## get_temperature()

```python
get_temperature(path: str)
```

temperature 값 조회

## set_temperature()

```python
set_temperature(path: str, value)
```

temperature 설정 또는 제거

---

# Viewport Selection API

## get_viewport_selection_enabled()

```python
get_viewport_selection_enabled() -> Optional[bool]
```

viewport 클릭 selection 가능 여부 반환

## set_viewport_selection_enabled()

```python
set_viewport_selection_enabled(enabled: bool) -> bool
```

viewport selection enable/disable 설정

## toggle_viewport_selection()

```python
toggle_viewport_selection() -> Optional[bool]
```

현재 상태 반전

---

# Frame API

## frame_prim()

```python
frame_prim(path: str) -> bool
```

단일 prim focus

## frame_prims()

```python
frame_prims(paths: List[str]) -> bool
```

여러 prim을 viewport에 맞게 frame
(내부적으로 1프레임 defer 후 비동기 실행)

---

# Selection API (Path 기반)

## get_selection()

```python
get_selection() -> List[str]
```

현재 선택된 prim 목록 반환 (stage path)

## clear_selection()

```python
clear_selection() -> bool
```

selection 초기화

## set_selection()

```python
set_selection(paths: List[str], expand_descendants: bool = False) -> bool
```

selection 교체

## add_to_selection()

```python
add_to_selection(paths: List[str], expand_descendants: bool = False) -> bool
```

selection 추가

---

# Leaf Name 기반 API (외부/Web UI 권장)

## select_by_leaf_names()

```python
select_by_leaf_names(
    leaf_names: List[str],
    mode: str = "replace",
    expand_descendants: bool = False,
    *,
    use_refresh: bool = False,
    require_unique: bool = False,
) -> Dict[str, Any]
```

Prim의 **leaf name 목록**을 전달하면 서비스가 캐시에서 stage path로 resolve하여 selection에 반영합니다.

- `mode`: `"replace" | "append" | "toggle"`
- `use_refresh=True`: resolve 전에 `refresh_cache()` 수행
- `require_unique=True`: 동일 leaf name이 여러 path로 매칭될 경우 `ok=False` 반환 (동작 수행 안 함)

리턴 예(요약):
- `resolved_paths`, `missing_names`, `ambiguous`, `selected`, `ok`

## clear_selection_by_leaf_names()

```python
clear_selection_by_leaf_names(
    leaf_names: List[str],
    *,
    use_refresh: bool = False,
    require_unique: bool = False,
) -> Dict[str, Any]
```

leaf name 목록에 해당하는 prim들을 **현재 selection에서 제거**합니다.
리턴: `removed`, `missing_names`, `ambiguous`, `ok`

## set_pickable_by_leaf_names()

```python
set_pickable_by_leaf_names(
    leaf_names: List[str],
    pickable: bool,
    *,
    use_refresh: bool = False,
    require_unique: bool = False,
) -> Dict[str, Any]
```

leaf name 목록에 해당하는 prim들에 대해 pickable을 **일괄 적용**합니다.
리턴: `updated`, `missing_names`, `ambiguous`, `ok`

---

# Usage Example

```python
from morph.pick_filter.service import ensure_service

svc = ensure_service()

pcb_leaf_names = [
    "N_01_PCB_On_Board",
    "N_02_PCB_Router",
    "N_03_Feeder",
    "N_04_PCB_Assembly",
    "N_05_Assembly",
    "N_06_Test",
    "N_07_Laser_Cutting",
]

svc.set_viewport_selection_enabled(False)

# leaf name 목록으로 pickable / selection 수행
svc.set_pickable_by_leaf_names(pcb_leaf_names, pickable=False)
svc.select_by_leaf_names(pcb_leaf_names, mode="replace")

# 현재 selection을 frame
svc.frame_prims(svc.get_selection())

# 특정 leaf name들만 selection에서 제거
svc.clear_selection_by_leaf_names(["N_06_Test", "N_07_Laser_Cutting"])
```

---

# 주의사항

1. `frame_prims()`는 비동기 실행됩니다.
2. active viewport가 없으면 frame/viewport selection 제어가 실패할 수 있습니다.
3. leaf name resolve는 캐시 기반(best-effort)이며, 동일 name이 복수 path에 존재할 수 있습니다(ambiguous).
4. temperature는 단순 메타데이터이며 알람/이벤트 발행은 하지 않습니다.
5. `stop()` 호출 시 viewport selection disable 상태를 복구합니다.
