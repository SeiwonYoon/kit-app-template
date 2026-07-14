"""morph.lam_control_1 Extension 인스턴스 싱글톤."""

from __future__ import annotations

from typing import Any, Optional

_INSTANCE: Optional[Any] = None


def get_lam_extension_instance() -> Optional[Any]:
    return _INSTANCE


def set_lam_extension_instance(ext: Any) -> None:
    global _INSTANCE
    _INSTANCE = ext


def clear_lam_extension_instance() -> None:
    global _INSTANCE
    _INSTANCE = None


def require_lam_extension_instance() -> Any:
    ext = _INSTANCE
    if ext is None:
        raise RuntimeError("morph.lam_control_1 extension is not active")
    return ext


__all__ = [
    "get_lam_extension_instance",
    "set_lam_extension_instance",
    "clear_lam_extension_instance",
    "require_lam_extension_instance",
]
