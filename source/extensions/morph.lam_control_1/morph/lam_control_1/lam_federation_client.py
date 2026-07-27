"""Federation query HTTP client — pagination, optional auth, fixture 모드."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .lam_api_timeline_parser import object_array_to_merged

_PRINT_PREFIX = "[LAM/federation]"

_SIMULATION_PATH_SUFFIX = "/api/v1/lam/simulations/simulations/"


def _fixture_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "federation_fixture"


def _mask_token(token: str) -> str:
    t = str(token or "").strip()
    if not t:
        return "(none)"
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}...{t[-4:]}"


def build_request_headers(
    *,
    bearer_token: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """HTTP 헤더 조립.

  - 인증 없음: token·extra_headers 모두 비우면 ``Content-Type`` 만 전송.
  - Bearer: ``FEDERATION_BEARER_TOKEN`` 또는 테스트 창 token 필드.
  - API key 등: ``FEDERATION_EXTRA_HEADERS`` 예 ``{"X-API-Key": "..."}``.
    """
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    tok = str(bearer_token or "").strip()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    for k, v in dict(extra_headers or {}).items():
        ks = str(k or "").strip()
        if ks:
            headers[ks] = str(v or "")
    return headers


def _log_response_sample(
    data: Dict[str, Any],
    *,
    row_sample: int,
    full: bool,
    quiet: bool = False,
) -> None:
    if quiet:
        return
    cols = list(data.get("columns") or [])
    rows = list(data.get("rows") or [])
    pag = dict(data.get("pagination") or {})
    print(
        f"{_PRINT_PREFIX} response: query_id={data.get('query_id')!r} "
        f"row_count={data.get('row_count')} len(rows)={len(rows)} pagination={pag}",
        flush=True,
    )
    print(f"{_PRINT_PREFIX} columns: {cols}", flush=True)
    if full:
        try:
            print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
        except Exception:
            pass
        return
    n = max(0, int(row_sample or 0))
    if n > 0 and rows:
        sample = rows[:n]
        print(f"{_PRINT_PREFIX} rows sample (first {len(sample)}):", flush=True)
        for i, row in enumerate(sample):
            print(f"  [{i}] {row}", flush=True)


def _load_fixture_page(offset: int) -> Dict[str, Any]:
    d = _fixture_dir()
    for name in (f"page_{offset}.json", "sample_mcc_target_prev_lot_history.json"):
        p = d / name
        if p.is_file():
            with p.open("r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(f"fixture not found under {d}")


def build_simulation_get_url(
    base_url: str,
    exec_id: str,
    *,
    offset: int,
    limit: int,
) -> str:
    """Simulation GET URL — ``{base}/api/v1/lam/simulations/simulations/{execId}?offset=&limit=``."""
    base = str(base_url or "").strip().rstrip("/")
    eid = urllib.parse.quote(str(exec_id or "").strip(), safe="")
    if not base or not eid:
        raise ValueError("base_url and exec_id are required for simulation GET")
    path = f"{base}{_SIMULATION_PATH_SUFFIX}{eid}"
    query = urllib.parse.urlencode(
        {"offset": max(0, int(offset or 0)), "limit": max(1, int(limit or 1))}
    )
    return f"{path}?{query}"


def parse_simulation_get_url(url: str) -> Tuple[str, str, int, int]:
    """전체 GET URL → ``(fab_base, exec_id, offset, limit)``."""
    parsed = urllib.parse.urlparse(str(url or "").strip())
    path = parsed.path or ""
    idx = path.find(_SIMULATION_PATH_SUFFIX)
    if idx < 0:
        raise ValueError(f"URL must contain {_SIMULATION_PATH_SUFFIX!r}")
    exec_id = path[idx + len(_SIMULATION_PATH_SUFFIX) :].strip("/")
    if not exec_id:
        raise ValueError("execId missing in simulation GET URL path")
    fab_base = f"{parsed.scheme}://{parsed.netloc}{path[:idx]}".rstrip("/")
    qs = urllib.parse.parse_qs(parsed.query or "")
    offset = int((qs.get("offset") or ["0"])[0])
    limit = max(1, int((qs.get("limit") or ["1000"])[0]))
    return fab_base, exec_id, max(0, offset), limit


def _http_get_json(
    url: str,
    *,
    timeout_sec: float,
) -> Tuple[int, Any, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        try:
            return status, json.loads(raw), raw
        except Exception:
            return status, None, raw
    except Exception as exc:
        return 0, None, str(exc)
    try:
        return status, json.loads(raw), raw
    except Exception:
        return status, None, raw


def _simulation_get_objects_from_payload(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    raise RuntimeError("simulation GET response must be a JSON array of objects")


def fetch_simulation_get_once(
    *,
    url: str,
    timeout_sec: float = 60.0,
) -> Tuple[int, Dict[str, Any], str]:
    """테스트 창용 — URL 그대로 GET 1회 → merged 형식."""
    status, data, raw = _http_get_json(url, timeout_sec=timeout_sec)
    if status and status >= 400:
        return status, {}, raw
    if data is None:
        return status, {}, raw
    try:
        objects = _simulation_get_objects_from_payload(data)
    except RuntimeError as exc:
        return status, {}, str(exc)
    merged = object_array_to_merged(objects)
    merged["http_status"] = status
    return status, merged, raw


def fetch_simulation_get_pages(
    *,
    base_url: str,
    exec_id: str,
    limit: int,
    initial_offset: int = 0,
    screen: int = 1,
    timeout_sec: float = 300.0,
    quiet: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``len(page) < limit`` 될 때까지 Simulation GET pagination 후 병합."""
    t0 = time.perf_counter()
    eid = str(exec_id or "").strip()
    if not eid:
        raise ValueError("exec_id is required for simulation GET")
    print(
        f"{_PRINT_PREFIX} simulation GET start screen={screen} execId={eid!r} "
        f"limit={limit}",
        flush=True,
    )
    all_objects: List[Dict[str, Any]] = []
    pages = 0
    offset = max(0, int(initial_offset or 0))
    first_offset = offset
    last_status = 0

    while True:
        page_url = build_simulation_get_url(
            base_url, eid, offset=offset, limit=limit
        )
        status, data, raw = _http_get_json(page_url, timeout_sec=timeout_sec)
        last_status = status
        pages += 1
        if status and status >= 400:
            raise RuntimeError(f"HTTP {status}: {raw[:500]}")
        objects = _simulation_get_objects_from_payload(data)
        all_objects.extend(objects)
        if not quiet:
            print(
                f"{_PRINT_PREFIX} simulation GET page {pages} offset={offset} "
                f"status={status} rows={len(objects)}",
                flush=True,
            )
        if len(objects) < int(limit):
            break
        offset += int(limit)
        if pages > 10000:
            raise RuntimeError("simulation GET pagination exceeded 10000 pages")

    elapsed = time.perf_counter() - t0
    merged = object_array_to_merged(all_objects)
    merged["exec_id"] = eid
    merged["fetch_mode"] = "simulation_get"
    meta = {
        "http_status": last_status,
        "pages": pages,
        "total_rows": len(all_objects),
        "elapsed_sec": elapsed,
        "screen": screen,
        "exec_id": eid,
        "fetch_mode": "simulation_get",
        "offset_start": first_offset,
        "limit": limit,
    }
    print(
        f"{_PRINT_PREFIX} simulation GET done screen={screen} execId={eid!r} "
        f"pages={pages} rows={len(all_objects)} elapsed={elapsed:.2f}s",
        flush=True,
    )
    return merged, meta


def fetch_simulation_get_pages_from_url(
    *,
    url: str,
    timeout_sec: float = 300.0,
    screen: int = 1,
    quiet: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """테스트 창 「GET 전체 fetch」 — URL 에서 base/execId/offset/limit 파싱."""
    fab_base, exec_id, offset, limit = parse_simulation_get_url(url)
    return fetch_simulation_get_pages(
        base_url=fab_base,
        exec_id=exec_id,
        limit=limit,
        initial_offset=offset,
        screen=screen,
        timeout_sec=timeout_sec,
        quiet=quiet,
    )


def _http_post_json(
    url: str,
    body: Dict[str, Any],
    *,
    headers: Dict[str, str],
    timeout_sec: float,
) -> Tuple[int, Dict[str, Any], str]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        raw = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        try:
            return status, json.loads(raw), raw
        except Exception:
            return status, {}, raw
    except Exception as exc:
        return 0, {}, str(exc)
    try:
        return status, json.loads(raw), raw
    except Exception:
        return status, {}, raw


def fetch_federation_pages(
    *,
    url: str,
    body: Dict[str, Any],
    limit: int,
    initial_offset: int = 0,
    screen: int = 1,
    bearer_token: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
    timeout_sec: float = 300.0,
    use_fixture: bool = False,
    log_row_sample: int = 5,
    log_full_response: bool = False,
    quiet: bool = False,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``has_next=false`` 까지 pagination fetch 후 rows 병합.

    ``quiet=True`` 이면 페이지별 columns/rows 샘플 로그를 생략하고
    start/done 요약만 남긴다 (``FEDERATION_VERBOSE_PARSE_LOG=False``).
    """
    t0 = time.perf_counter()
    headers = build_request_headers(bearer_token=bearer_token, extra_headers=extra_headers)
    safe_body = {k: v for k, v in dict(body or {}).items()}
    print(
        f"{_PRINT_PREFIX} fetch start screen={screen} url={url!r} limit={limit} "
        f"token={_mask_token(bearer_token)}"
        + ("" if quiet else f" body={safe_body}"),
        flush=True,
    )
    merged_columns: List[str] = []
    merged_rows: List[List[Any]] = []
    merged_base: Dict[str, Any] = {}
    pages = 0
    offset = max(0, int(initial_offset or 0))
    first_offset = offset
    last_status = 0
    last_meta: Dict[str, Any] = {}

    while True:
        page_body = dict(body or {})
        page_body["limit"] = int(limit)
        page_body["offset"] = int(offset)
        if use_fixture:
            data = _load_fixture_page(offset)
            status = 200
            raw = ""
        else:
            status, data, raw = _http_post_json(
                url, page_body, headers=headers, timeout_sec=timeout_sec
            )
        last_status = status
        pages += 1
        if status and status >= 400:
            raise RuntimeError(f"HTTP {status}: {raw[:500]}")
        if not isinstance(data, dict):
            raise RuntimeError(f"invalid JSON response at offset={offset}")
        cols = list(data.get("columns") or [])
        rows = list(data.get("rows") or [])
        if not merged_base:
            # query_id/execution_mode 등 서버 원본 메타도 최종 원본 JSON에 보존한다.
            merged_base = {
                k: v
                for k, v in data.items()
                if k not in ("columns", "rows", "row_count", "pagination")
            }
        if not merged_columns and cols:
            merged_columns = cols
        merged_rows.extend(rows)
        pag = dict(data.get("pagination") or {})
        has_next = bool(pag.get("has_next"))
        last_meta = {
            "query_id": data.get("query_id"),
            "row_count": data.get("row_count"),
            "page_rows": len(rows),
            "pagination": pag,
        }
        if not quiet:
            print(
                f"{_PRINT_PREFIX} page {pages} offset={offset} status={status} "
                f"rows={len(rows)} has_next={has_next} meta={last_meta}",
                flush=True,
            )
            _log_response_sample(
                data, row_sample=log_row_sample, full=log_full_response, quiet=False
            )
        if not has_next:
            break
        # Federation offset은 페이지 번호가 아니라 rows 행 오프셋이다.
        offset += int(pag.get("limit") or limit or 1)
        if pages > 10000:
            raise RuntimeError("pagination exceeded 10000 pages")

    elapsed = time.perf_counter() - t0
    merged = {
        **merged_base,
        "query_id": last_meta.get("query_id") or merged_base.get("query_id"),
        "columns": merged_columns,
        "rows": merged_rows,
        "row_count": len(merged_rows),
        "pagination": {
            "limit": limit,
            "offset": first_offset,
            "has_next": False,
        },
    }
    meta = {
        "http_status": last_status,
        "pages": pages,
        "total_rows": len(merged_rows),
        "elapsed_sec": elapsed,
        "screen": screen,
    }
    print(
        f"{_PRINT_PREFIX} fetch done screen={screen} pages={pages} "
        f"rows={len(merged_rows)} elapsed={elapsed:.2f}s",
        flush=True,
    )
    return merged, meta


def fetch_single_post(
    *,
    url: str,
    body: Dict[str, Any],
    limit: int,
    offset: int = 0,
    bearer_token: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
    timeout_sec: float = 60.0,
    use_fixture: bool = False,
) -> Tuple[int, Dict[str, Any], str]:
    """테스트 창용 — pagination 없이 POST 1회."""
    page_body = dict(body or {})
    page_body["limit"] = max(1, int(limit or 1))
    page_body["offset"] = max(0, int(offset or 0))
    if use_fixture:
        return 200, _load_fixture_page(page_body["offset"]), ""
    headers = build_request_headers(bearer_token=bearer_token, extra_headers=extra_headers)
    return _http_post_json(url, page_body, headers=headers, timeout_sec=timeout_sec)


__all__ = [
    "build_request_headers",
    "build_simulation_get_url",
    "fetch_federation_pages",
    "fetch_simulation_get_once",
    "fetch_simulation_get_pages",
    "fetch_simulation_get_pages_from_url",
    "fetch_single_post",
    "parse_simulation_get_url",
]
