# morph.manual_nav

- `morph.manual_nav`는 Viewport의 확장 메뉴 Navigation Visor bar의 카메라 제어 기능을 api 형태로 사용하기 위한 익스텐션입니다.
---

## 1. 제공 기능

- Teleport

### 1.1 예정

- Orbit (X) 구현되지 않음
	- 현재 버전 기준 teleport와 달리, tool api로 공개되지 않은 상태.

---

## 2. 외부에서 호출하는 방법

### 2.1 service 얻기 (권장)

`morph.manual_nav`를 로드 후 사용합니다.

```python
from morph.manual_nav.extension import get_service

	svc = get_service()
        if not svc:
            print("ManualNavService not ready - UI skipped")
            return
```
---

### 2.2 api 사용

```python
	svc.teleport_on() 
   	svc.teleport_off()
```

- teleport_on, teleport_off는 각각 텔레포트 on off 상태를 제어합니다.
- navigation bar의 텔레포트 기능과 동일하게 작동합니다.
- navigation bar의 dependency와 관계 없이 동작합니다.
- 만약 navigation bar가 켜져 있어 orbit 혹은 기타 상태일 경우, 해당 상태를 제어하지 않습니다. (teleport_on 호출시 이전 상태를 자동으로 꺼 주지 않음)
