"""LAM 확장 `data/` 디렉터리 경로 해석 (SSOT).

`lam/` 레포 루트 탐색 대신 ``{extension}/data/`` 아래 ``csv``,
``lam_event_sequences``, ``usd`` 등을 기준으로 한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def extension_data_root() -> Path:
    """확장 패키지 루트의 ``data/`` 폴더 (존재 여부와 무관)."""
    here = Path(__file__).resolve().parent
    return here.parent.parent / "data"


def resolve_local_data_path(file_path: PathLike | None) -> str | None:
    """``data/`` 기준 상대 경로·절대 경로·Nucleus URL 을 해석한다.

    - ``omniverse://...`` — 그대로 반환.
    - 절대 로컬 경로 — 존재하면 정규화된 문자열, 없으면 ``None``.
    - 그 외 — ``{extension}/data/<file_path>`` 가 존재하면 절대 경로 문자열.
    """
    from .lam_usd_path import is_omniverse_usd_url

    s = (str(file_path) if file_path is not None else "").strip()
    if not s:
        return None
    if is_omniverse_usd_url(s):
        return s

    p = Path(s)
    if p.is_absolute():
        return str(p.resolve()) if p.exists() else None

    candidate = extension_data_root() / s
    if candidate.exists():
        return str(candidate.resolve())
    return None


def resolve_local_data_path_or_default(file_path: PathLike) -> Path:
    """존재하지 않아도 ``data/`` 아래 경로를 ``Path`` 로 반환 (디렉터리 생성용)."""
    resolved = resolve_local_data_path(file_path)
    if resolved is not None:
        return Path(resolved)
    return extension_data_root() / str(file_path).strip()


__all__ = [
    "extension_data_root",
    "resolve_local_data_path",
    "resolve_local_data_path_or_default",
]
