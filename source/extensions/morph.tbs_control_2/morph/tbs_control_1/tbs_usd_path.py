"""Master / 합성 USD 경로 해석 — 로컬 파일 + ``omniverse://`` URL."""

from __future__ import annotations

import os


def is_omniverse_usd_url(path: str) -> bool:
    """Nucleus 등 Omniverse USD URL 인지."""
    return (path or "").strip().lower().startswith("omniverse://")


def master_usd_path_is_openable(path: str) -> bool:
    """Open Master 에 넘길 수 있는 경로인지 (로컬 존재 또는 omniverse URL)."""
    p = (path or "").strip()
    if not p:
        return False
    if is_omniverse_usd_url(p):
        return True
    return os.path.isfile(p)


__all__ = ["is_omniverse_usd_url", "master_usd_path_is_openable"]
