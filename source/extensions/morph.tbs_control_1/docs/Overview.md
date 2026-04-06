# TBS Control 1

TBS Control과 동일한 동작을 하는 확장이며, 코드 구조만 기능별 모듈로 나누어 두었습니다.

- **USD Load**: load_window 모듈(제어창 상단에 embed). 경로 입력, resource 콤보, Load.
- **TBS 제어창**: control_window 모듈. USD 타임라인, 가상 시그널, XML 제너레이터, prim 목록, button_0/1/2.
- **3D 정보 패널**: selection_overlay + viewport_overlay. 뷰포트 선택 연동 및 오버레이.
- **시퀀스 편집기**: sequence_editor 모듈. Step 추가/편집/실행, JSON 저장·로드.
