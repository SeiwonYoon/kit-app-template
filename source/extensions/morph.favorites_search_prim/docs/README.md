# morph.favorites_search_prim

`morph.favorites_search_prim` 확장 문서입니다.

## 주요 기능
- 좌측 즐겨찾기 목록 / 우측 Stage 검색 패널(1:2 분할)
- 우측 Stage의 별 버튼으로 즐겨찾기 추가/삭제
- 좌측 목록 클릭: 선택, 더블클릭: 뷰포트 포커스
- 즐겨찾기 로컬 JSON 저장/로드(`Load`, `AllClear` 버튼)

## 저장 경로
- `%LOCALAPPDATA%\ov\data\Kit\<app_name>\<app_version>\favorites_search_prim.json`

## 코드 구조
- `morph/favorites_search_prim/extension.py`: 확장 생명주기, 데이터/이벤트/저장 로직
- `morph/favorites_search_prim/ui_layout.py`: UI 레이아웃/렌더링 함수

## 상세 문서
- `source/extensions/morph.favorites_search_prim/docs/README.md`
