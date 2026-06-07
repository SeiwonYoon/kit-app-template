"""TBS 확장 ``data/`` 디렉터리 경로 해석 (lam_control ``lam_data_paths`` 와 동일 패턴)."""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


def extension_data_root() -> Path:
    """확장 패키지 루트의 ``data/`` 폴더 (존재 여부와 무관)."""
    here = Path(__file__).resolve().parent
    return here.parent.parent / "data"


def resolve_local_data_path(file_path: PathLike | None) -> str | None:
    """``data/`` 기준 상대 경로·절대 경로·URL 을 해석한다.

    - ``omniverse://...`` / ``http(s)://...`` — 그대로 반환.
    - 절대 로컬 경로 — 존재하면 정규화된 문자열, 없으면 ``None``.
    - 그 외 — ``{extension}/data/<file_path>`` 가 존재하면 절대 경로 문자열.
    """
    s = (str(file_path) if file_path is not None else "").strip()
    if not s:
        return None
    low = s.lower()
    if low.startswith("omniverse://") or low.startswith("http://") or low.startswith("https://"):
        return s

    p = Path(s)
    if p.is_absolute():
        return str(p.resolve()) if p.exists() else None

    candidate = extension_data_root() / s
    if candidate.exists():
        return str(candidate.resolve())
    return None


__all__ = [
    "extension_data_root",
    "resolve_local_data_path",
]
