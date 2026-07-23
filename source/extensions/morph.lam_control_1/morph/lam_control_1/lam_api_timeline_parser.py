"""Federation API rows/columns → ``ParsedCsvRow`` / ``DwellRecord``."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .simulation_play import (
    DwellRecord,
    ParsedCsvRow,
    build_lot_id_to_foup_index,
    normalize_csv_timeline,
    parse_time_to_seconds,
    rows_to_dwell_records,
    sort_dwells_for_playback,
)

_PRINT_PREFIX = "[LAM/api-parser]"


def _is_nonempty_config(cfg: Any) -> bool:
    if not isinstance(cfg, dict):
        return False
    return any(str(v or "").strip() for v in cfg.values())


def normalize_configs(configs: Any) -> Tuple[List[Dict[str, Any]], bool, bool]:
    """``configs`` 배열 → 길이 2, 화면1·2 표시 여부."""
    raw = list(configs) if isinstance(configs, list) else []
    while len(raw) < 2:
        raw.append({})
    raw = raw[:2]
    bodies: List[Dict[str, Any]] = []
    for item in raw:
        bodies.append(dict(item) if isinstance(item, dict) else {})
    show_1 = _is_nonempty_config(bodies[0])
    show_2 = _is_nonempty_config(bodies[1])
    return bodies, show_1, show_2


def api_row_to_dict(columns: List[str], row: List[Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for i, col in enumerate(columns):
        if i < len(row):
            out[str(col)] = row[i]
    return out


def rows_to_parsed_csv_rows(
    columns: List[str],
    rows: List[List[Any]],
    *,
    eqp_id: str,
) -> Tuple[List[ParsedCsvRow], int, int]:
    """API merged rows → ``ParsedCsvRow`` (정규화 전)."""
    parsed: List[ParsedCsvRow] = []
    skipped = 0
    dup = 0
    seen: set = set()
    eqp = str(eqp_id or "").strip()
    for raw_row in rows or []:
        if not isinstance(raw_row, (list, tuple)):
            skipped += 1
            continue
        raw = api_row_to_dict(columns, list(raw_row))
        mod = str(raw.get("module_nm") or "").strip()
        if not mod:
            skipped += 1
            continue
        cs_raw = raw.get("cassette_slot")
        if cs_raw is None or str(cs_raw).strip() == "":
            skipped += 1
            continue
        try:
            cs = int(str(cs_raw).strip())
        except Exception:
            skipped += 1
            continue
        start = parse_time_to_seconds(raw.get("eqp_start_tm"))
        end = parse_time_to_seconds(raw.get("eqp_end_tm"))
        lot = str(raw.get("lot_id") or "").strip()
        pt_raw = raw.get("process_tm") or "0"
        key = (lot, cs, mod, start, end)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
        parsed.append(
            ParsedCsvRow(
                eqp_id=eqp,
                module_nm=mod,
                lot_id=lot,
                cassette_slot=cs,
                eqp_start_tm=float(start),
                eqp_end_tm=float(end),
                process_tm=parse_time_to_seconds(pt_raw),
            )
        )
    return parsed, skipped, dup


def merged_response_to_dwells(
    merged: Dict[str, Any],
    *,
    eqp_id: str,
    quiet: bool = False,
) -> Tuple[List[DwellRecord], Dict[str, Any]]:
    """병합 API 응답 → dwell 타임라인 + 파싱 통계."""
    columns = list(merged.get("columns") or [])
    rows = list(merged.get("rows") or [])
    parsed, skipped, dup = rows_to_parsed_csv_rows(columns, rows, eqp_id=eqp_id)
    normalized = normalize_csv_timeline(parsed)
    lot_map = build_lot_id_to_foup_index(normalized)
    dwells = sort_dwells_for_playback(rows_to_dwell_records(normalized, lot_map))
    stats = {
        "input_rows": len(rows),
        "parsed_rows": len(parsed),
        "normalized_rows": len(normalized),
        "dwells": len(dwells),
        "skipped": skipped,
        "duplicates": dup,
        "lots_to_foup": lot_map,
    }
    if not quiet:
        print(
            f"{_PRINT_PREFIX} parse: rows={stats['input_rows']} parsed={stats['parsed_rows']} "
            f"dwells={stats['dwells']} skip={skipped} dup={dup}",
            flush=True,
        )
    return dwells, stats


def federation_virtual_path(screen: int, body: Dict[str, Any]) -> "Path":
    """Federation 재생 캐시 키용 **가상** Path.

    디스크에 ``data/api_queries`` 를 만들거나 파일을 읽지 않는다.
    (배포에서 ext 경로 mkdir → PermissionError 로 시뮬 전체가 중단되던 원인.)
    """
    from pathlib import Path

    eqp = str(body.get("eqp_id") or "eqp").strip() or "eqp"
    lot = str(body.get("lot_id") or "lot").strip() or "lot"
    safe = re.sub(r"[^\w\-.]+", "_", f"s{int(screen)}_{eqp}_{lot}")[:120]
    # 로컬 상대 가상 경로 — resolve 만 하며 mkdir/open 하지 않음
    return Path("lam_federation_virtual") / f"{safe}.virtual"


__all__ = [
    "api_row_to_dict",
    "federation_virtual_path",
    "merged_response_to_dwells",
    "normalize_configs",
    "rows_to_parsed_csv_rows",
]
