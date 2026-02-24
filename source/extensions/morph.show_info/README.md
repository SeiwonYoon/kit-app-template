# morph.show_info

선택된 USD prim 옆에 3D 정보 패널을 띄우는 확장입니다.

---

## Public API 규격

다른 확장 또는 메시징 핸들러에서 호출할 수 있는 공개 API입니다.

### 인스턴스 획득

```text
morph.show_info.get_instance() -> Extension | None
```

- 확장이 로드된 후에만 `Extension` 인스턴스를 반환합니다. 비활성 시 `None`.

### 메서드 (Extension 인스턴스 기준)

| 메서드 | 설명 | 인자 | 반환 |
|--------|------|------|------|
| (규격은 구현 후 여기에 작성) | | | |

예시 형식:

- `get_open_paths() -> List[str]` — 현재 열린 패널의 prim 경로 목록.
- `add_panels_for_paths(paths: List[str]) -> None` — 해당 경로들에 대해 3D 패널 표시.
- `close_panel(path_str: str) -> None` — 해당 경로의 패널 닫기.

---

## 웹 연동 (이벤트 매핑)

웹에서 위 API를 사용할 때는 메시징 확장을 통해 이벤트로 호출합니다.
요청 이벤트 → 핸들러가 위 API 호출 → 응답 이벤트로 결과 전달.

| API | 요청 이벤트 | payload (요청) | 응답 이벤트 | payload (응답) |
|-----|-------------|----------------|-------------|----------------|
| (구현 후 매핑 작성) | | | | |

- 상세한 “웹에서의 사용 방법”은 프로젝트의 `data/show-info-web-usage.md` 참고.
