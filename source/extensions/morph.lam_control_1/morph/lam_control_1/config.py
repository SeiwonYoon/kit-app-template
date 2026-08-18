"""환경별 호스트 — 저장소 최상단 루트 ``.env`` 로드.

로컬 / 개발 / 운영 각각 리포 루트 ``.env`` 에
``FEDERATION_QUERY_URL``, ``FEDERATION_SIMULATION_GET_BASE_URL``,
``NUCLEUS_HOST_KEY`` 호스트만 두고, 이 모듈에서 공통 path 를 이어 붙인다.

python-dotenv 없이 KEY=VALUE 만 파싱한다 (Kit 의존성 추가 없음).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# 환경과 무관한 공통 path (호스트만 .env)
# ---------------------------------------------------------------------------
# Nucleus USD — ``usd_query_url()`` = NUCLEUS_HOST_KEY + 이 path
USD_QUERY_URL: str = "/Projects/lam"
# POST Federation query
FEDERATION_QUERY_PATH: str = "/queries/mcc-target-prev-lot-history/run"
# Simulation GET — ``lam_federation_client.build_simulation_get_url`` 가 base 뒤에 붙임
# ``/api/v1/lam/simulations/{exec_id}``

_ENV_CACHE: Optional[Dict[str, str]] = None


def repo_root() -> Path:
    """저장소 최상단 (``repo.toml`` / ``.git`` / ``source/extensions`` 기준)."""
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / "source" / "extensions").is_dir() and (
            (cand / "repo.toml").is_file()
            or (cand / ".git").exists()
            or (cand / ".env").is_file()
            or (cand / ".env.example").is_file()
        ):
            return cand
    # fallback: .../morph/lam_control_1/config.py → parents[5]
    try:
        return here.parents[5]
    except IndexError:
        return here.parents[-1]


def env_file_path() -> Path:
    return repo_root() / ".env"


def _parse_env_file(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            out[key] = val
    return out


def load_env(*, force_reload: bool = False) -> Dict[str, str]:
    """``.env`` + 이미 설정된 ``os.environ`` (environ 이 우선)."""
    global _ENV_CACHE
    if _ENV_CACHE is not None and not force_reload:
        return dict(_ENV_CACHE)
    file_vals = _parse_env_file(env_file_path())
    merged: Dict[str, str] = dict(file_vals)
    for k, v in os.environ.items():
        if not str(v or "").strip():
            continue
        if k.startswith("FEDERATION_") or k in (
            "NUCLEUS_HOST_KEY",
            "OMNI_USER",
            "OMNI_PASS",
        ):
            merged[k] = str(v).strip()
    _ENV_CACHE = merged
    return dict(merged)


def _get(key: str, default: str = "") -> str:
    vals = load_env()
    v = str(vals.get(key, "") or "").strip()
    if v:
        return v
    env_v = str(os.environ.get(key, "") or "").strip()
    if env_v:
        return env_v
    return str(default or "").strip()


def _join_url(base: str, path: str) -> str:
    b = (base or "").strip().rstrip("/")
    p = (path or "").strip()
    if not p:
        return b
    if not p.startswith("/"):
        p = "/" + p
    return f"{b}{p}"


def federation_query_host(*, default: str = "") -> str:
    """``.env`` 의 ``FEDERATION_QUERY_URL`` 호스트 (path 제외)."""
    return _get("FEDERATION_QUERY_URL", default).rstrip("/")


def federation_simulation_get_host(*, default: str = "") -> str:
    """``.env`` 의 ``FEDERATION_SIMULATION_GET_BASE_URL`` 호스트/베이스."""
    return _get("FEDERATION_SIMULATION_GET_BASE_URL", default).rstrip("/")


def federation_query_url(
    *,
    default_host: str = "",
    query_path: str = FEDERATION_QUERY_PATH,
) -> str:
    """defaults ``FEDERATION_QUERY_URL`` — 호스트 + 공통 query path."""
    return _join_url(federation_query_host(default=default_host), query_path)


def federation_simulation_get_base_url(*, default_host: str = "") -> str:
    """defaults ``FEDERATION_SIMULATION_GET_BASE_URL`` — GET path 는 client 가 이어 붙임."""
    return federation_simulation_get_host(default=default_host)


def usd_query_host(*, default: str = "") -> str:
    """``.env`` 의 ``NUCLEUS_HOST_KEY`` (path 제외)."""
    return _get("NUCLEUS_HOST_KEY", default).rstrip("/")


def usd_query_url(
    *,
    default_host: str = "",
    query_path: str = USD_QUERY_URL,
) -> str:
    """Nucleus USD 베이스 — 호스트 + ``USD_QUERY_URL`` (``/Projects/lam``)."""
    return _join_url(usd_query_host(default=default_host), query_path)


__all__ = [
    "FEDERATION_QUERY_PATH",
    "USD_QUERY_URL",
    "env_file_path",
    "federation_query_host",
    "federation_query_url",
    "federation_simulation_get_base_url",
    "federation_simulation_get_host",
    "load_env",
    "repo_root",
    "usd_query_host",
    "usd_query_url",
]
