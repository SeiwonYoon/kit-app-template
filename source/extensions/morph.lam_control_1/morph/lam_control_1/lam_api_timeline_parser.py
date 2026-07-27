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

# camelCase 대응 시 alias 추가 — 상세: docs/lam_control_federation_get_camelcase_field_guide_ko.md
_API_ROW_FIELD_ALIASES: Dict[str, Tuple[str, ...]] = {
    "module_nm": ("module_nm", "moduleNm"),
    "lot_id": ("lot_id", "lotId"),
    "eqp_id": ("eqp_id", "eqpId"),
    "cassette_slot": ("cassette_slot", "cassetteSlot"),
    "eqp_start_tm": ("eqp_start_tm", "eqpStartTm"),
    "eqp_end_tm": ("eqp_end_tm", "eqpEndTm"),
    "process_tm": ("process_tm", "processTm"),
}


def config_use_simulation_get(body: Dict[str, Any]) -> bool:
    """``configs[n]`` 에 비어 있지 않은 ``execId`` 가 있으면 Simulation GET 경로."""
    return bool(str((body or {}).get("execId") or "").strip())


def normalize_api_row_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """API row dict 키를 snake_case canonical 로 정규화 (alias 지원)."""
    if not isinstance(row, dict):
        return {}
    out = dict(row)
    for canonical, aliases in _API_ROW_FIELD_ALIASES.items():
        if str(out.get(canonical) or "").strip():
            continue
        for alias in aliases:
            if alias == canonical:
                continue
            if alias in row and str(row.get(alias) or "").strip():
                out[canonical] = row[alias]
                break
    return out


def object_array_to_merged(objects: List[Any]) -> Dict[str, Any]:
    """Simulation GET ``[{...}, ...]`` → POST 와 동일한 ``columns``/``rows`` 병합 형식."""
    rows_norm = [
        normalize_api_row_dict(o) for o in (objects or []) if isinstance(o, dict)
    ]
    columns: List[str] = []
    seen_cols: set = set()
    for row in rows_norm:
        for key in row.keys():
            ks = str(key)
            if ks not in seen_cols:
                seen_cols.add(ks)
                columns.append(ks)
    matrix = [[row.get(col) for col in columns] for row in rows_norm]
    return {
        "columns": columns,
        "rows": matrix,
        "row_count": len(matrix),
        "fetch_mode": "simulation_get",
    }


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
    eqp_id_from_rows: bool = False,
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
        raw = normalize_api_row_dict(api_row_to_dict(columns, list(raw_row)))
        row_eqp = str(raw.get("eqp_id") or "").strip() if eqp_id_from_rows else eqp
        if not row_eqp:
            skipped += 1
            continue
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
                eqp_id=row_eqp,
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
    eqp_id_from_rows: bool = False,
    quiet: bool = False,
) -> Tuple[List[DwellRecord], Dict[str, Any]]:
    """병합 API 응답 → dwell 타임라인 + 파싱 통계."""
    columns = list(merged.get("columns") or [])
    rows = list(merged.get("rows") or [])
    parsed, skipped, dup = rows_to_parsed_csv_rows(
        columns,
        rows,
        eqp_id=eqp_id,
        eqp_id_from_rows=eqp_id_from_rows,
    )
    normalized = normalize_csv_timeline(parsed)
    lot_map = build_lot_id_to_foup_index(normalized)
    dwells = sort_dwells_for_playback(rows_to_dwell_records(normalized, lot_map))
    eqp_ids = sorted({str(r.eqp_id or "").strip() for r in parsed if str(r.eqp_id or "").strip()})
    stats = {
        "input_rows": len(rows),
        "parsed_rows": len(parsed),
        "normalized_rows": len(normalized),
        "dwells": len(dwells),
        "skipped": skipped,
        "duplicates": dup,
        "lots_to_foup": lot_map,
        "eqp_ids": eqp_ids,
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

    exec_id = str(body.get("execId") or "").strip()
    if exec_id:
        safe = re.sub(r"[^\w\-.]+", "_", f"s{int(screen)}_exec_{exec_id}")[:120]
        return Path("lam_federation_virtual") / f"{safe}.virtual"
    eqp = str(body.get("eqp_id") or "eqp").strip() or "eqp"
    lot = str(body.get("lot_id") or "lot").strip() or "lot"
    safe = re.sub(r"[^\w\-.]+", "_", f"s{int(screen)}_{eqp}_{lot}")[:120]
    # 로컬 상대 가상 경로 — resolve 만 하며 mkdir/open 하지 않음
    return Path("lam_federation_virtual") / f"{safe}.virtual"


__all__ = [
    "api_row_to_dict",
    "config_use_simulation_get",
    "federation_virtual_path",
    "merged_response_to_dwells",
    "normalize_api_row_dict",
    "normalize_configs",
    "object_array_to_merged",
    "rows_to_parsed_csv_rows",
]
