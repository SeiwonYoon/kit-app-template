# morph.section_control

`morph.section_control`은 Kit의 Section(단면) 기능을 코드로 제어하기 위한 익스텐션입니다.
외부(다른 익스텐션/스크립트)에서 이 익스텐션의 **서비스 API를 직접 호출**하는 방식으로 사용합니다.

---

## 1. 제공 기능

- Section 상태 관리/조회
  - enabled / axis / flip / offset
- Stage 준비 상태에 따라 재시도 적용
  - 변경된 값은 post_update 루프에서 ready 될 때까지 apply 재시도
- 깜빡임 최소화 warm-up
  - enable을 ON으로 전환하는 순간 1회만 show/hide warm-up 수행

---

## 2. 외부에서 호출하는 방법

### 2.1 service 얻기 (권장)

`morph.section_control`이 로드/활성화된 상태에서 아래처럼 사용합니다.

```python
from morph.section_control.extension import get_service

svc = get_service()
if not svc:
    raise RuntimeError("section_control service is not ready (extension not started?)")

print(svc.get_state())
```

> **주의**: `get_service()`가 `None`이면, 아직 익스텐션이 시작되지 않았거나 종료된 상태입니다.
> 외부에서 필요하다면 `ExtensionManager`로 해당 익스텐션을 먼저 enable 시키는 흐름을 추가하세요.

---

### 2.2 전체 값 설정

```python
svc.set_all(
    enabled=True,
    axis="X",
    flip=False,
    offset=10.0,
    reason="external_set_all"
)
```

- `set_all()`은 상태를 갱신하고, 값이 변경되면 자동으로 apply 예약을 수행합니다.
- 실제 USD Stage 반영은 post_update에서 ready 될 때까지 재시도 적용됩니다.

---

### 2.3 부분 설정 (편의 메서드)

```python
svc.set_enabled(True, reason="external_enabled")
svc.set_axis("Y", reason="external_axis")
svc.set_flip(True, reason="external_flip")
svc.set_offset(-25.0, reason="external_offset")
```

---

### 2.4 apply 예약만 수행 (특수 상황)

일반적으로 `set_*()`이 자동으로 apply 예약을 수행하므로, 보통 필요 없습니다.
다만 외부에서 controller의 dirty를 직접 건드린 경우 등 특수 상황에서 사용합니다.

```python
svc.apply_now(reason="force_apply", retries=240)
```

---

## 3. API 요약

### SectionControlService (public)

- `get_state() -> dict`
- `set_all(enabled: bool, axis: str, flip: bool, offset: float, reason: str = ...) -> dict`
- `set_enabled(enabled: bool, reason: str = ...) -> dict`
- `set_axis(axis: str, reason: str = ...) -> dict`
- `set_flip(flip: bool, reason: str = ...) -> dict`
- `set_offset(offset: float, reason: str = ...) -> dict`
- `apply_now(reason: str = ..., retries: int = 240) -> None`
- `ensure_section_backend_running(force: bool = False) -> bool`

---

## 4. 동작 메커니즘(간단 설명)

1. `set_all()` / `set_axis()` 등 호출 → 내부 상태값 갱신 (`SectionController` dirty 플래그 설정)
2. 값이 변경되면 `schedule_apply()` 자동 호출
3. post_update 루프에서 `controller.apply_once_if_possible()` 재시도
   - stage/SectionManager/widget prim이 준비되면 axis/offset을 실제 stage에 반영
4. enable이 OFF인 상태에서도 manipulator 토글은 강제로 OFF 유지

---

## 5. 체크리스트

- [ ] `morph.section_control` 익스텐션이 enable 되어 있는가?
- [ ] `get_service()`가 None이 아닌가?
- [ ] `axis`는 `"X" | "Y" | "Z"` 중 하나인가?
- [ ] `offset`은 float로 변환 가능한가?
- [ ] stage가 로드되지 않은 시점이면 apply가 지연될 수 있음(ready 될 때까지 재시도)
