"""
morph.tbs_control_1 패키지

【역할】
- extension 모듈 re-export (패키지 import 시 확장 API 노출).

【수정 가이드】
- 확장 진입점·메뉴: extension.py, 상위 extension.toml

【유지보수 체크포인트】
- 새 모듈 추가 시 이 파일은 보통 수정 불필요(직접 import 권장).
- 패키지 import 정책을 바꾸려면:
  · wildcard export 유지 여부(`from .extension import *`)
  · 명시 export(`__all__`) 도입 여부 검토
- 확장 로딩 실패 디버깅 순서:
  1) extension.toml의 python.module name 확인
  2) extension.py의 on_startup import 에러 확인
  3) 본 파일 re-export 순환참조 여부 확인
"""

from .extension import *
