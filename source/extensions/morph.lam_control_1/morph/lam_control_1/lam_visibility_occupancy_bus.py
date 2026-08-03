"""PRIM_VISIBILITY 적용 직후 읽기 전용 fan-out.

시퀀스 엔진이 wafer 숨김/보임을 적용한 **뒤에만** 호출한다.
구독자는 상태를 관찰·표시만 하며, USD/스케줄/재생 시각을 바꾸지 않는다.

추가 관찰자(평면도 등)는 이 버스에만 붙이면 된다 — 시뮬 동작 변경과 무관하게
실제 visibility 이벤트에 동기화된다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence


def notify_wafer_visibility_applied(
    paths: Sequence[str],
    visible: bool,
    ctx: Optional[Dict[str, Any]],
    *,
    screen: int = 1,
) -> None:
    """visibility 적용 완료 후 호출. 예외는 삼켜 시뮬 경로에 영향 없음."""
    if not ctx or not paths:
        return
    si = max(1, int(screen or 1))
    ctx_map = dict(ctx) if isinstance(ctx, dict) else {}
    if not ctx_map:
        return

    # 1) 평면도 점유 — 항상 (3D 라벨 UI on/off 와 무관)
    try:
        from .lam_floorplan_occupancy import get_floorplan_occupancy

        occ = get_floorplan_occupancy(si)
        for p in paths:
            try:
                occ.on_visibility(str(p or ""), bool(visible), ctx_map, screen=si)
            except Exception:
                pass
    except Exception:
        pass

    # 2) 3D 웨이퍼 번호 트래커 — 기존 게이트 유지
    try:
        from .lam_wafer_viewport_labels import (
            get_wafer_label_tracker,
            wafer_label_tracking_enabled,
        )

        if wafer_label_tracking_enabled():
            tracker = get_wafer_label_tracker(si)
            for p in paths:
                try:
                    tracker.on_visibility(str(p or ""), bool(visible), ctx_map, screen=si)
                except Exception:
                    pass
    except Exception:
        pass


__all__ = ["notify_wafer_visibility_applied"]
