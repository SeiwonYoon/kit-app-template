"""
morph.tbs_control_1 패키지

【역할】
- extension 모듈 re-export (패키지 import 시 확장 API 노출).

【수정 가이드】
- 확장 진입점·메뉴: extension.py, 상위 extension.toml
"""

from .extension import *
