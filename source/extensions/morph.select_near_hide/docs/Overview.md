# Overview

Select Near Hide는 morph.base_ui.kit 앱의 확장으로, 화면에서 prim을 선택할 때 **같은 부모의 다른 자식(sibling)** prim들을 자동으로 비활성화(숨김)하거나 반투명하게 처리합니다.

## 기능

- **활성화**: 체크박스로 기능 ON/OFF
- **비활성화(숨김) 모드**: 선택된 prim의 sibling prim들을 `visibility=invisible`로 숨김
- **반투명 모드**: 선택된 prim의 sibling prim들에 `displayOpacity`를 적용해 반투명 처리
- **모두 복원**: 숨김/반투명 처리한 prim들을 원래 상태로 복원

## 사용 방법

1. **Select Near Hide** 창을 엽니다 (오른쪽 상단 도킹).
2. **활성화** 체크박스를 선택합니다.
3. **비활성화** 또는 **반투명** 모드를 선택합니다.
4. Stage/뷰포트에서 prim을 선택하면 sibling prim들이 자동으로 숨겨지거나 반투명해집니다.
5. **모두 복원** 버튼으로 이전 상태로 되돌릴 수 있습니다.

모든 변경은 USD **session layer**에 기록되므로, 원본 stage 데이터는 수정되지 않습니다.