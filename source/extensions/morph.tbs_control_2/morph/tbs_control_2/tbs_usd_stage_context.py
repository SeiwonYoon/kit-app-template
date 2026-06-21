"""LAM / 분할 화면 — thread-local USD context → stage 조회."""

from __future__ import annotations

import threading
from typing import Any, Optional

import omni.usd as ou  # noqa: E402

_ctx = threading.local()


def push_usd_context_name(usd_context_name: Optional[str]) -> Optional[str]:
    prev = getattr(_ctx, "usd_context_name", None)
    cn = str(usd_context_name or "").strip()
    if cn:
        _ctx.usd_context_name = cn
    elif hasattr(_ctx, "usd_context_name"):
        try:
            del _ctx.usd_context_name
        except Exception:
            _ctx.usd_context_name = None
    return prev if prev else None


def pop_usd_context_name(prev: Optional[str]) -> None:
    if prev and str(prev).strip():
        _ctx.usd_context_name = str(prev).strip()
    elif hasattr(_ctx, "usd_context_name"):
        try:
            del _ctx.usd_context_name
        except Exception:
            _ctx.usd_context_name = None


def get_current_usd_context_name() -> Optional[str]:
    cn = getattr(_ctx, "usd_context_name", None)
    return str(cn).strip() if cn and str(cn).strip() else None


def anim_key(prim_path: str, usd_context_name: Optional[str]) -> str:
    return f"{(usd_context_name or '').strip()}\x00{prim_path}"


def prim_path_from_anim_key(key: str) -> str:
    parts = str(key or "").split("\x00", 1)
    return parts[1] if len(parts) > 1 else parts[0]


def get_stage_for_thread_context() -> Any:
    cn = getattr(_ctx, "usd_context_name", None)
    try:
        if cn and str(cn).strip():
            ctx = ou.get_context(str(cn).strip())
        else:
            ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None


def get_stage_for_context_name(usd_context_name: Optional[str]) -> Any:
    cn = str(usd_context_name or "").strip()
    try:
        if cn:
            ctx = ou.get_context(cn)
        else:
            ctx = ou.get_context()
        return ctx.get_stage() if ctx else None
    except Exception:
        return None
