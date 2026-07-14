"""CSV 프리런 — plan 빌드 → 타임라인 SSOT → (옵션) ``data/csv_prerun`` JSON export."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

_PRINT_PREFIX = "[LAM/csv-prerun]"


@dataclass(frozen=True)
class CsvTimelineItem:
    """프리런 타임라인 한 항목 (CSV 시각 기준)."""

    t: float
    kind: str  # "dwell" | "event"
    label: str
    json_path: str = ""
    schedule_row_id: str = ""
    category: str = ""


@dataclass(frozen=True)
class CsvPreRunResult:
    """화면 1개분 CSV 프리런 결과."""

    screen: int
    csv_path: Path
    mtime_ns: int
    size: int
    config_tag: str
    final_csv_time_sec: float
    build_ms: float
    items: Tuple[CsvTimelineItem, ...]


def csv_prerun_export_dir() -> Path:
    """``morph.lam_control_1/data/csv_prerun``."""
    return Path(__file__).resolve().parents[2] / "data" / "csv_prerun"


def fingerprint_from_source(
    path: Path,
    *,
    mtime_ns: int,
    size: int,
    config_tag: str,
) -> Dict[str, Any]:
    return {
        "csv_path": str(path.resolve()),
        "mtime_ns": int(mtime_ns),
        "size": int(size),
        "config_tag": str(config_tag or ""),
    }


def _final_csv_time_sec(schedule: List[Any]) -> float:
    best = 0.0
    for ent in schedule or []:
        try:
            best = max(best, float(getattr(ent, "time_sec", 0.0) or 0.0))
        except Exception:
            continue
    return best


def build_timeline_items_from_cached(cached: Any) -> Tuple[CsvTimelineItem, ...]:
    """``CachedCsvPlayback`` schedule → 프리런 타임라인."""
    out: List[CsvTimelineItem] = []
    for ent in list(getattr(cached, "schedule", None) or []):
        cat = str(getattr(ent, "category", "") or "")
        kind = "dwell" if cat == "dwell" else "event"
        label = str(
            getattr(ent, "title_ko", "")
            or getattr(ent, "event_name", "")
            or getattr(ent, "module_nm", "")
            or ""
        )
        json_path = ""
        if kind == "event":
            json_path = str(getattr(ent, "json_path", "") or getattr(ent, "event_json", "") or "")
        row_id = str(getattr(ent, "schedule_row", "") or getattr(ent, "row_id", "") or "")
        try:
            t = float(getattr(ent, "time_sec", 0.0) or 0.0)
        except Exception:
            t = 0.0
        out.append(
            CsvTimelineItem(
                t=t,
                kind=kind,
                label=label,
                json_path=json_path,
                schedule_row_id=row_id,
                category=cat,
            )
        )
    out.sort(key=lambda it: (it.t, it.kind, it.label))
    return tuple(out)


def build_prerun_result_from_cached(
    cached: Any,
    *,
    screen: int = 1,
) -> CsvPreRunResult:
    items = build_timeline_items_from_cached(cached)
    path = Path(getattr(cached, "path"))
    return CsvPreRunResult(
        screen=int(screen),
        csv_path=path,
        mtime_ns=int(getattr(cached, "mtime_ns", 0) or 0),
        size=int(getattr(cached, "size", 0) or 0),
        config_tag=str(getattr(cached, "config_tag", "") or ""),
        final_csv_time_sec=_final_csv_time_sec(list(getattr(cached, "schedule", None) or [])),
        build_ms=float(getattr(cached, "build_ms", 0.0) or 0.0),
        items=items,
    )


def build_csv_prerun_export_document(result: CsvPreRunResult) -> Dict[str, Any]:
    """v1 최소 스키마 + ``extensions`` 확장 슬롯."""
    return {
        "version": 1,
        "screen": int(result.screen),
        "source": fingerprint_from_source(
            result.csv_path,
            mtime_ns=result.mtime_ns,
            size=result.size,
            config_tag=result.config_tag,
        ),
        "summary": {
            "final_csv_time_sec": float(result.final_csv_time_sec),
            "item_count": len(result.items),
            "build_ms": float(result.build_ms),
        },
        "timeline": [
            {
                "t": float(it.t),
                "kind": it.kind,
                "label": it.label,
                "json_path": it.json_path,
                "schedule_row_id": it.schedule_row_id,
                "category": it.category,
            }
            for it in result.items
        ],
        "extensions": {},
    }


def write_csv_prerun_export_json(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def maybe_export_csv_prerun_json(
    result: CsvPreRunResult,
    *,
    export_enabled: bool,
) -> Optional[Path]:
    if not export_enabled:
        return None
    out_dir = csv_prerun_export_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"prerun_screen{int(result.screen)}_{stamp}.json"
    doc = build_csv_prerun_export_document(result)
    write_csv_prerun_export_json(out_path, doc)
    try:
        print(f"{_PRINT_PREFIX} export JSON (화면{result.screen}): {out_path}", flush=True)
    except Exception:
        pass
    return out_path


def run_csv_prerun_build(
    path: Path,
    *,
    screen: int = 1,
    progress_tick: Optional[Callable[[int, int], None]] = None,
    export_enabled: Optional[bool] = None,
) -> Tuple[Any, CsvPreRunResult]:
    """CSV 파싱 + plan 빌드(프리런) → ``CachedCsvPlayback`` + ``CsvPreRunResult``."""
    from .simulation_play import build_and_cache_csv_playback

    if export_enabled is None:
        try:
            from .lam_sim_control_defaults import CSV_PRERUN_EXPORT_JSON

            export_enabled = bool(CSV_PRERUN_EXPORT_JSON)
        except Exception:
            export_enabled = True

    t0 = time.perf_counter()
    cached = build_and_cache_csv_playback(path, progress_tick=progress_tick)
    result = build_prerun_result_from_cached(cached, screen=screen)
    result = CsvPreRunResult(
        screen=result.screen,
        csv_path=result.csv_path,
        mtime_ns=result.mtime_ns,
        size=result.size,
        config_tag=result.config_tag,
        final_csv_time_sec=result.final_csv_time_sec,
        build_ms=(time.perf_counter() - t0) * 1000.0,
        items=result.items,
    )
    maybe_export_csv_prerun_json(result, export_enabled=bool(export_enabled))
    return cached, result


__all__ = [
    "CsvTimelineItem",
    "CsvPreRunResult",
    "build_csv_prerun_export_document",
    "build_prerun_result_from_cached",
    "build_timeline_items_from_cached",
    "csv_prerun_export_dir",
    "fingerprint_from_source",
    "maybe_export_csv_prerun_json",
    "run_csv_prerun_build",
    "write_csv_prerun_export_json",
]
