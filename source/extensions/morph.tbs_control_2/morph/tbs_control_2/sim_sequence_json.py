"""
``data/sim_sequences/*.json`` 경로 해석 — omni/carb 없이 프리런·plan 빌드에서 사용.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from .json_playback_timing import renewal_info_from_json_path
from .sequence_renewal import find_first_renewal_index


def extension_data_roots() -> Tuple[Path, ...]:
    """``morph.tbs_control_2/data/sim_sequences`` 를 포함할 수 있는 확장 루트."""
    here = Path(__file__).resolve()
    roots: List[Path] = []
    seen: set = set()

    def _add(p: Path) -> None:
        try:
            rp = p.resolve()
        except Exception:
            rp = Path(p)
        key = str(rp).lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(rp)

    # .../source/extensions/morph.tbs_control_2
    _add(here.parents[2])
    # .../morph.tbs_control_2 (editable install layout)
    if len(here.parents) > 3:
        _add(here.parents[3])

    try:
        import carb

        rt = carb.tokens.get_tokens_interface().resolve("${root}")
        if rt:
            _add(Path(rt) / "source" / "extensions" / "morph.tbs_control_2")
    except Exception:
        pass

    return tuple(roots)


def resolve_sim_sequence_json_path(path_text: str) -> Optional[Path]:
    """``data/sim_sequences/foo.json`` 또는 ``foo.json`` → 실제 파일 Path."""
    raw = str(path_text or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_file():
        return p
    if p.is_absolute():
        return p if p.is_file() else None
    rel = p
    if rel.parts and rel.parts[0].lower() == "data":
        pass
    else:
        rel = Path("data") / "sim_sequences" / rel.name
    for root in extension_data_roots():
        cand = root / rel
        try:
            if cand.is_file():
                return cand.resolve()
        except Exception:
            if cand.is_file():
                return cand
        cand2 = root / "data" / "sim_sequences" / p.name
        try:
            if cand2.is_file():
                return cand2.resolve()
        except Exception:
            if cand2.is_file():
                return cand2
    return None


def resolve_renewal_for_json_step(
    *,
    json_path: Optional[str] = None,
    parsed_steps: Optional[List[Any]] = None,
    json_basename: str = "",
    linked: str = "",
) -> Tuple[bool, Optional[float], Optional[str]]:
    """
    ``(has_renewal, offset_sec_1x, resolved_json_path)`` — 스케줄·plan SSOT.

    offset 을 알 수 없으면 ``has_renewal=False`` (0초 fallback 으로 JSON 시작 시 갱신 방지).
    """
    if isinstance(parsed_steps, list) and parsed_steps:
        try:
            from .json_playback_timing import renewal_info_from_steps
            from .sequence_renewal import find_first_renewal_index

            if find_first_renewal_index(list(parsed_steps)) is not None:
                hr, off = renewal_info_from_steps(list(parsed_steps))
                if hr and off is not None and float(off) > 1e-9:
                    jp = resolve_sim_sequence_json_path(
                        str(json_path or json_basename or linked or "")
                    )
                    return True, float(off), str(jp) if jp is not None else (str(json_path) if json_path else None)
                if hr:
                    jp = resolve_sim_sequence_json_path(
                        str(json_path or json_basename or linked or "")
                    )
                    return True, None, str(jp) if jp is not None else (str(json_path) if json_path else None)
        except Exception:
            pass

    for cand in (json_path, json_basename, linked):
        cs = str(cand or "").strip()
        if not cs:
            continue
        jp = resolve_sim_sequence_json_path(cs)
        if jp is None:
            continue
        steps = load_sim_sequence_steps(str(jp))
        if not isinstance(steps, list) or not steps:
            continue
        try:
            from .json_playback_timing import renewal_info_from_steps
            from .sequence_renewal import find_first_renewal_index

            if find_first_renewal_index(steps) is None:
                continue
            hr, off = renewal_info_from_steps(steps)
            if hr and off is not None and float(off) > 1e-9:
                return True, float(off), str(jp)
            if hr:
                return True, None, str(jp)
        except Exception:
            pass
    return False, None, None


def renewal_info_from_basename_or_path(path_text: str) -> Tuple[bool, Optional[float], Optional[str]]:
    """``(has_renewal, offset_sec, absolute_json_path)``."""
    jp = resolve_sim_sequence_json_path(path_text)
    if jp is None:
        return False, None, None
    has_r, off = renewal_info_from_json_path(str(jp))
    return bool(has_r), off, str(jp)


def load_sim_sequence_steps(path_text: str) -> Optional[List]:
    jp = resolve_sim_sequence_json_path(path_text)
    if jp is None:
        return None
    try:
        parsed = json.loads(jp.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, list) else None
    except Exception:
        return None


def has_renewal_marker_in_file(path_text: str) -> bool:
    steps = load_sim_sequence_steps(path_text)
    if not steps:
        return False
    return find_first_renewal_index(steps) is not None


__all__ = [
    "extension_data_roots",
    "has_renewal_marker_in_file",
    "load_sim_sequence_steps",
    "renewal_info_from_basename_or_path",
    "resolve_renewal_for_json_step",
    "resolve_sim_sequence_json_path",
]
