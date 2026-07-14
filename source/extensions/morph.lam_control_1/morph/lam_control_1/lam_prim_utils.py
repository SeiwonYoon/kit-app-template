"""USD stage helpers (TBS prim_utils 패턴 포팅)."""

from __future__ import annotations

from typing import Any, Optional


def get_stage() -> Optional[Any]:
    try:
        import omni.usd as ou

        ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


__all__ = ["get_stage"]
