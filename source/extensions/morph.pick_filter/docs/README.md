# morph.pick_filter Extension

## Overview

`morph.pick_filter`는 NVIDIA Omniverse Kit 기반 애플리케이션에서\
Prim의 Pick 가능 여부 제어, Selection 제어, Viewport Selection
비활성화,\
Group Selection, Frame(Focus), Temperature 메타데이터 관리 기능을
제공하는 서비스형 익스텐션입니다.

외부 익스텐션 또는 Web UI는 `PickFilterService`를 통해 기능을 호출하도록
설계되어 있습니다.

------------------------------------------------------------------------

# Service Entry Point

## ensure_service()

``` python
ensure_service() -> PickFilterService
```

서비스 인스턴스를 안전하게 생성 및 시작합니다.\
이미 존재하는 경우 기존 인스턴스를 반환합니다.

------------------------------------------------------------------------

# Lifecycle API

## start()

``` python
start() -> None
```

서비스 시작 (중복 호출 안전)

## stop()

``` python
stop() -> None
```

서비스 종료 및 viewport 상태 복구

------------------------------------------------------------------------

# Cache API

## get_revision()

``` python
get_revision() -> int
```

현재 캐시 revision 반환

## get_items_cached()

``` python
get_items_cached() -> List[Dict[str, Any]]
```

현재 캐시된 prim 목록 반환

## refresh_cache()

``` python
refresh_cache() -> List[Dict[str, Any]]
```

스테이지 재스캔 후 캐시 갱신

------------------------------------------------------------------------

# Pickable API

## set_pickable()

``` python
set_pickable(path: str, pickable: bool, include_descendants: bool = False)
```

특정 prim의 pick 가능 여부 설정

## set_pickable_bulk()

``` python
set_pickable_bulk(paths: List[str], pickable: bool)
```

여러 prim 일괄 적용

## lock_all()

``` python
lock_all()
```

전체 pick 비활성화

## unlock_all()

``` python
unlock_all()
```

전체 pick 활성화

------------------------------------------------------------------------

# Temperature API

## get_temperature()

``` python
get_temperature(path: str)
```

temperature 값 조회

## set_temperature()

``` python
set_temperature(path: str, value)
```

temperature 설정 또는 제거

------------------------------------------------------------------------

# Viewport Selection API

## get_viewport_selection_enabled()

``` python
get_viewport_selection_enabled() -> Optional[bool]
```

viewport 클릭 selection 가능 여부 반환

## set_viewport_selection_enabled()

``` python
set_viewport_selection_enabled(enabled: bool) -> bool
```

viewport selection enable/disable 설정

## toggle_viewport_selection()

``` python
toggle_viewport_selection() -> Optional[bool]
```

현재 상태 반전

------------------------------------------------------------------------

# Frame API

## frame_prim()

``` python
frame_prim(path: str) -> bool
```

단일 prim focus

## frame_prims()

``` python
frame_prims(paths: List[str]) -> bool
```

여러 prim을 viewport에 맞게 frame

------------------------------------------------------------------------

# Selection API

## get_selection()

``` python
get_selection() -> List[str]
```

현재 선택된 prim 목록 반환

## clear_selection()

``` python
clear_selection() -> bool
```

selection 초기화

## set_selection()

``` python
set_selection(paths: List[str], expand_descendants: bool = False) -> bool
```

selection 교체

## add_to_selection()

``` python
add_to_selection(paths: List[str], expand_descendants: bool = False) -> bool
```

selection 추가

------------------------------------------------------------------------

# Group API

## list_groups()

``` python
list_groups() -> List[Dict[str, Any]]
```

정의된 그룹 목록 반환

## get_group_members()

``` python
get_group_members(group_id: str) -> List[str]
```

group을 현재 stage path로 resolve

## select_group()

``` python
select_group(group_id: str, mode: str = "replace", expand_descendants: bool = False) -> Dict[str, Any]
```

group prim을 selection에 반영

------------------------------------------------------------------------

# Usage Example

``` python
from morph.pick_filter.service import ensure_service

svc = ensure_service()

svc.set_viewport_selection_enabled(False)
svc.set_pickable_for_group("pcb_steps", False)
svc.select_group("pcb_steps", mode="replace")
svc.frame_prims(svc.get_selection())
```

------------------------------------------------------------------------

# 주의사항

1.  `frame_prims()`는 비동기 실행됩니다.\
2.  active viewport가 없으면 selection 제어 실패 가능\
3.  group은 leaf name 기반 resolve\
4.  temperature는 단순 메타 데이터\
5.  stop() 호출 시 viewport 상태 복구
