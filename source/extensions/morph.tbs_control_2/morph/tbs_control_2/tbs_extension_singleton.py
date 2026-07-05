"""morph.tbs_control_2 Extension 인스턴스 싱글톤 — HyView 메시징·HTTP 브리지 등에서 공유."""

from __future__ import annotations

from typing import Any, Optional

_INSTANCE: Optional[Any] = None


def get_tbs_extension_instance() -> Optional[Any]:
    return _INSTANCE


def set_tbs_extension_instance(ext: Any) -> None:
    global _INSTANCE
    _INSTANCE = ext


def clear_tbs_extension_instance() -> None:
    global _INSTANCE
    _INSTANCE = None


def require_tbs_extension_instance() -> Any:
    ext = _INSTANCE
    if ext is None:
        raise RuntimeError("morph.tbs_control_2 extension is not active")
    return ext
