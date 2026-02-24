# morph.measure_control

`morph.measure_control`는 지정한 USD Prim 경로에 대해 `omni.kit.tool.measure`의 Mesh BBox 측정(X/Y/Z)을 실행하는 브리지(extension)입니다.

## 1. 제공 기능

- Prim 경로 입력 기반 Mesh 측정 실행
- 내부적으로 `omni.kit.tool.measure.viewport.tools.mesh` 기능 호출
- 실패 시 원인 메시지 반환(경로 없음, mesh 없음, backend 미준비 등)

## 2. 선행 조건

- `morph.measure_control` extension이 활성화되어 있어야 함
- `omni.kit.tool.measure` extension이 설치/활성화 가능해야 함
- 대상 Prim은 Mesh이거나 하위에 Mesh를 포함해야 함

## 3. 외부(다른 extension / web bridge)에서 사용 방법

### 3.1 서비스 가져오기

```python
from morph.measure_control.extension import get_service

svc = get_service()
if not svc:
    raise RuntimeError("measure_control service is not ready")
```

### 3.2 Prim 경로로 Mesh 측정 실행

```python
result = svc.measure_mesh_for_prim_path("/World/Cube")
print(result)
# 성공 예: {"ok": True, "message": "mesh bbox measurement created for /World/Cube", "path": "/World/Cube"}
# 실패 예: {"ok": False, "message": "invalid prim path: /World/Cube"}
```

### 3.3 응답 포맷

- `ok: bool`  
  실행 성공 여부
- `message: str`  
  성공/실패 상세 메시지
- `path: str`  
  성공 시 요청 경로

## 4. Web에서 연동할 때 권장 패턴

웹 서버(또는 메시징 핸들러)에서 전달받은 prim path를 그대로 서비스에 넘기고, 반환값을 JSON으로 응답합니다.

```python
# pseudo code
path = request_json.get("prim_path", "")
result = svc.measure_mesh_for_prim_path(path)
return result
```

권장 검증:

- 빈 문자열 경로 차단
- `/World/...` 형태 경로 검증
- 실패 시 `result["message"]`를 그대로 클라이언트에 전달

## 5. 현재 Dummy UI 동작

- 버튼 클릭 시 고정 경로 `/World/Cube`에 대해 측정 실행
- 결과는 Status 필드와 로그(`[measure_control]`, `[measure_control.ui]`)에 표시

## 6. 트러블슈팅

- `invalid prim path`: 경로에 해당하는 prim이 stage에 없음
- `mesh not found under prim`: prim/하위에 mesh가 없음
- `backend is not available`: `omni.kit.tool.measure` 활성화 실패 또는 로드 실패
- `measure mesh api import failed`: Measure extension API import 실패(의존성/버전 확인 필요)
