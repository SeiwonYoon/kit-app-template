# Extension Handler 추가 가이드

## 📝 개요
BaseHandler를 상속받아 새로운 익스텐션 핸들러를 추가하는 방법을 설명합니다.

---

## 🚀 핸들러 추가 방법 (2단계)

### 1단계: 핸들러 파일 생성
`extension_handlers/{extensionName}_handler.py` 파일 생성

```python
import carb
import carb.events
from typing import Dict, Callable, List
from .base_handler import BaseHandler

class YourExtensionHandler(BaseHandler):
    """익스텐션과의 메시지 통신을 처리하는 클래스"""
    
    def __init__(self):
        # 필요한 익스텐션 인스턴스 가져오기 (선택사항)
        # self._your_extension = your_extension.get_instance()
        super().__init__()
    
    def get_outgoing_events(self) -> List[str]:
        """클라이언트로 보낼 이벤트 리스트 (선택사항)"""
        return [
            "yourEventComplete",
            "yourEventError",
        ]
    
    def get_event_handlers(self) -> Dict[str, Callable]:
        """클라이언트에서 받을 이벤트 핸들러 맵 (필수)"""
        return {
            'yourEventRequest': self._on_your_event,
            'anotherRequest': self._on_another_event,
        }
    
    def _on_your_event(self, event: carb.events.IEvent) -> None:
        """이벤트 핸들러"""
        # 페이로드 검증
        if "required_field" not in event.payload:
            carb.log_error("Missing 'required_field' in payload")
            return
        
        # 이벤트 처리 로직
        data = event.payload["required_field"]
        carb.log_info(f"Received: {data}")
        
        # 클라이언트로 응답 전송
        self.dispatch_event("yourEventComplete", {"result": "success", "data": data})
    
    def _on_another_event(self, event: carb.events.IEvent) -> None:
        """다른 이벤트 핸들러"""
        carb.log_info("Another event received")
```

### 2단계: HANDLERS 리스트에 추가
`extension_handlers/__init__.py` 파일 수정

```python
from .base_handler import BaseHandler
from .usdLoader_handler import UsdLoaderHandler
from .yourExtension_handler import YourExtensionHandler  # import 추가

# 여기에 새 핸들러를 추가하세요
HANDLERS = [
    UsdLoaderHandler,
    YourExtensionHandler,  # 추가
]

__all__ = ["BaseHandler", "UsdLoaderHandler", "YourExtensionHandler", "HANDLERS"]
```

**끝!** extension.py는 수정하지 않아도 됩니다.

---

## 🔧 BaseHandler 기능

BaseHandler가 자동으로 처리하는 것들:

1. **Outgoing 이벤트 등록**
   - `get_outgoing_events()`에서 반환한 이벤트 자동 등록
   - 클라이언트로 메시지를 보낼 준비 완료

2. **Incoming 이벤트 등록**
   - `get_event_handlers()`에서 반환한 이벤트 자동 등록 및 구독

3. **이벤트 전송 헬퍼**
   - `self.dispatch_event()` 메서드로 간편하게 이벤트 전송

4. **리소스 정리**
   - `on_shutdown()`에서 자동으로 구독 해제

5. **로깅**
   - 초기화, 이벤트 전송, 종료 시 자동 로깅

---

## 📋 이벤트 타입

### Incoming Events (클라이언트 → 서버)
클라이언트에서 요청을 받는 이벤트

```python
def get_event_handlers(self) -> Dict[str, Callable]:
    return {
        'loadUSD': self._on_load_usd,
        'selectPrim': self._on_select_prim,
    }
```

### Outgoing Events (서버 → 클라이언트)
클라이언트로 응답/알림을 보내는 이벤트

```python
def get_outgoing_events(self) -> List[str]:
    return [
        "usdLoadComplete",
        "usdLoadError",
    ]
```

### 이벤트 전송 방법

```python
# BaseHandler의 헬퍼 메서드 사용 (추천)
self.dispatch_event("usdLoadComplete", {"result": "success", "path": path})
```

---

## 📝 실전 예제: PickFilter 핸들러

### pickFilter_handler.py

```python
import carb
import carb.events
from typing import Dict, Callable, List
from .base_handler import BaseHandler

class PickFilterHandler(BaseHandler):
    """pick_filter 익스텐션과의 메시지 통신을 처리하는 클래스"""
    
    def __init__(self):
        self._filter_enabled = False
        super().__init__()
    
    def get_outgoing_events(self) -> List[str]:
        return [
            "pickFilterStatusChanged",
            "pickFilterError",
        ]
    
    def get_event_handlers(self) -> Dict[str, Callable]:
        return {
            'enablePickFilter': self._on_enable,
            'disablePickFilter': self._on_disable,
            'getPickFilterStatus': self._on_get_status,
        }
    
    def _on_enable(self, event: carb.events.IEvent) -> None:
        self._filter_enabled = True
        carb.log_info("Pick filter enabled")
        self.dispatch_event("pickFilterStatusChanged", {"enabled": True})
    
    def _on_disable(self, event: carb.events.IEvent) -> None:
        self._filter_enabled = False
        carb.log_info("Pick filter disabled")
        self.dispatch_event("pickFilterStatusChanged", {"enabled": False})
    
    def _on_get_status(self, event: carb.events.IEvent) -> None:
        self.dispatch_event("pickFilterStatusChanged", {"enabled": self._filter_enabled})
```

### __init__.py에 추가

```python
from .pickFilter_handler import PickFilterHandler

HANDLERS = [
    UsdLoaderHandler,
    PickFilterHandler,
]
```

---

## 📋 네이밍 규칙

| 구분 | 형식 | 예시 |
|------|------|------|
| 파일명 | `{extensionName}_handler.py` | `usdLoader_handler.py` |
| 클래스명 | `{ExtensionName}Handler` | `UsdLoaderHandler` |
| Incoming 이벤트 | `{action}Request` | `loadUSDRequest` |
| Outgoing 이벤트 | `{action}Complete/Error` | `loadUSDComplete` |

---

## ✅ 체크리스트

- [ ] `BaseHandler`를 상속받는 클래스 생성
- [ ] `get_event_handlers()` 메서드 구현 (필수)
- [ ] `get_outgoing_events()` 메서드 구현 (선택)
- [ ] 이벤트 핸들러 메서드 작성
- [ ] `self.dispatch_event()`로 응답 전송
- [ ] `__init__.py`의 `HANDLERS` 리스트에 추가

---

## 📁 폴더 구조

```
extension_handlers/
├── __init__.py                  (HANDLERS 리스트 관리)
├── base_handler.py              (공통 로직)
├── usdLoader_handler.py         (예제)
└── yourExtension_handler.py     ← 새 핸들러
```
