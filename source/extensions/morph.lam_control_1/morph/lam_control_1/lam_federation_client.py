"""Federation query HTTP client — pagination, optional auth, fixture 모드."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_PRINT_PREFIX = "[LAM/federation]"


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


def _log_response_sample(data: Dict[str, Any], *, row_sample: int, full: bool) -> None:
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
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """``has_next=false`` 까지 pagination fetch 후 rows 병합."""
    t0 = time.perf_counter()
    headers = build_request_headers(bearer_token=bearer_token, extra_headers=extra_headers)
    safe_body = {k: v for k, v in dict(body or {}).items()}
    print(
        f"{_PRINT_PREFIX} fetch start screen={screen} url={url!r} limit={limit} "
        f"token={_mask_token(bearer_token)} body={safe_body}",
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
        print(
            f"{_PRINT_PREFIX} page {pages} offset={offset} status={status} "
            f"rows={len(rows)} has_next={has_next} meta={last_meta}",
            flush=True,
        )
        _log_response_sample(data, row_sample=log_row_sample, full=log_full_response)
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
    "fetch_federation_pages",
    "fetch_single_post",
]
