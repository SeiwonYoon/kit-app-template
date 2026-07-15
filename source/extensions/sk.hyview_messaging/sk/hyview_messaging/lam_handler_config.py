"""Federation fetch limit — 웹 T2V 경로 SSOT.

실무에서 페이지 크기를 바꿀 때 이 값을 수정한다.
``morph.lam_control_1.lam_sim_control_defaults.FEDERATION_FETCH_LIMIT`` 보다
우선한다 (bridge 가 여기를 먼저 읽음).
"""

FEDERATION_FETCH_LIMIT: int = 50

__all__ = ["FEDERATION_FETCH_LIMIT"]
