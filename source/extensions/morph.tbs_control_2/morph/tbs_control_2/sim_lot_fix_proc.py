"""LOT별 OHT↔EP 고정 공정시간 — 파싱·표시 헬퍼."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class LotFixProcEntry:
    """fix 공정 입력 한 행 (N번째 행 → LOT_{N:03d})."""

    label: str
    oht_ep_sec: float
    ep_oht_sec: float
    valid: bool = True


def parse_lot_fix_proc_text(text: str, lot_count: int) -> List[LotFixProcEntry]:
    """
    ``이름, oht초, ep_oht초`` 형식 파싱.

    - 빈 텍스트 → 빈 리스트 (호출자: fix 미적용)
    - 잘못된 행 → ``valid=False`` (해당 LOT만 랜덤)
    - ``lot_count`` 초과 행 무시
    """
    raw = str(text or "").strip()
    if not raw:
        return []
    try:
        cap = max(1, int(lot_count))
    except Exception:
        cap = 1
    rows: List[LotFixProcEntry] = []
    for line in raw.splitlines():
        if len(rows) >= cap:
            break
        line = str(line or "").strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            rows.append(LotFixProcEntry(label=parts[0] if parts else "", oht_ep_sec=0.0, ep_oht_sec=0.0, valid=False))
            continue
        label = parts[0]
        try:
            oht = float(parts[1])
            ep = float(parts[2])
            if oht <= 0.0 or ep <= 0.0:
                raise ValueError("non-positive")
            rows.append(LotFixProcEntry(label=label, oht_ep_sec=oht, ep_oht_sec=ep, valid=True))
        except Exception:
            rows.append(LotFixProcEntry(label=label, oht_ep_sec=0.0, ep_oht_sec=0.0, valid=False))
    return rows


def format_lot_id_display(lot_id: str, label: str = "") -> str:
    """fix 라벨이 있으면 ``LOT_001(tacny80)`` 형식."""
    lid = str(lot_id or "").strip()
    if not lid:
        return ""
    lab = str(label or "").strip()
    if lab:
        return f"{lid}({lab})"
    return lid


def lot_id_from_payload(payload: Optional[Dict[str, Any]]) -> str:
    """이벤트·진행 payload에서 사용자 표시용 lot_id."""
    if not isinstance(payload, dict):
        return ""
    disp = str(payload.get("lot_id_display") or "").strip()
    if disp:
        return disp
    lot_id = str(payload.get("lot_id") or "").strip()
    if not lot_id:
        return ""
    label = str(payload.get("lot_fix_label") or "").strip()
    return format_lot_id_display(lot_id, label)


def format_fix_meta_block(
    *,
    lot_id: str = "",
    lot_id_display: str = "",
    fix_oht_ep: Optional[float] = None,
    fix_ep_oht: Optional[float] = None,
) -> str:
    """타임테이블 fix 메타 ``{lot:LOT_001(tacny80),fix_oht_ep:586}``."""
    lot_disp = str(lot_id_display or "").strip() or format_lot_id_display(lot_id, "")
    parts: List[str] = []
    if lot_disp:
        parts.append(f"lot:{lot_disp}")
    if fix_oht_ep is not None:
        parts.append(f"fix_oht_ep:{_fmt_fix_num(fix_oht_ep)}")
    if fix_ep_oht is not None:
        parts.append(f"fix_ep_oht:{_fmt_fix_num(fix_ep_oht)}")
    if len(parts) <= 1:
        return ""
    return "{" + ",".join(parts) + "}"


def _fmt_fix_num(v: Any) -> str:
    try:
        f = float(v)
    except Exception:
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:g}"


def read_lot_fix_proc_at_start(ext: Any) -> Optional[Tuple[LotFixProcEntry, ...]]:
    """시뮬 시작 시 fix 창 텍스트 스냅샷. 비어 있으면 ``None`` (기존 동작 유지)."""
    mdl = getattr(ext, "_sim_fix_proc_text_model", None)
    if mdl is None:
        return None
    try:
        text = str(mdl.get_value_as_string() or "")
    except Exception:
        text = ""
    if not str(text).strip():
        return None
    try:
        lot_count = max(1, int(ext._sim_lot_count_model.get_value_as_int()))
    except Exception:
        lot_count = 6
    rows = parse_lot_fix_proc_text(text, lot_count)
    if not rows:
        return None
    return tuple(rows)
