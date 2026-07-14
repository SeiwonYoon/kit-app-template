"""화면별(USD context) 채널 모션 스코프 — TBS ``sim_channel_scope`` 경량 포팅."""

from __future__ import annotations

from typing import Any, Optional


def stop_channel_animations(
    usd_context_name: Optional[str],
    *,
    diag_reason: str = "",
) -> None:
    """지정 USD context 의 translate/rotate 만 중지."""
    try:
        from . import lam_rotate_animation as _lrx
        from . import lam_translate_animation as _ltx

        cn = str(usd_context_name or "").strip()
        if cn:
            _ltx.stop_translate_animations_for_context(cn)
            _lrx.stop_rotate_animations_for_context(cn)
        else:
            _ltx.stop_all_translate_animations()
            _lrx.stop_all_rotate_animations()
    except Exception:
        pass
    if diag_reason:
        print(f"[LAM/ChannelScope] stop ctx={usd_context_name!r} reason={diag_reason}", flush=True)


def is_channel_motion_busy(
    usd_context_name: Optional[str],
    registry: Any = None,
) -> bool:
    """해당 context 에 진행 중인 MOVE/ROTATE 가 있는지."""
    _ = registry
    try:
        from . import lam_rotate_animation as _lrx
        from . import lam_translate_animation as _ltx

        cn = str(usd_context_name or "").strip() or None
        if cn:
            for _k, state in list(getattr(_ltx, "_animations", {}).items()):
                if str(state.get("usd_context_name") or "").strip() == cn:
                    return True
            for _k, state in list(getattr(_lrx, "_rot_animations", {}).items()):
                if str(state.get("usd_context_name") or "").strip() == cn:
                    return True
            return False
        return bool(_ltx.is_translate_animation_running() or _lrx.is_rotate_animation_running())
    except Exception:
        return False


__all__ = [
    "is_channel_motion_busy",
    "stop_channel_animations",
]
