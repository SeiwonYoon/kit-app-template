"""fix 공정 txt 파일 — Viewport HUD 드롭다운·파일사용 체크박스."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Tuple

import omni.ui as ui


def fix_proc_txt_dir() -> Path:
    """``data/txt`` — 확장 루트 기준 fix 공정 txt 폴더."""
    return Path(__file__).resolve().parents[2] / "data" / "txt"


def list_fix_proc_txt_files() -> List[str]:
    d = fix_proc_txt_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.glob("*.txt") if p.is_file())


def ensure_fix_proc_file_hud_models(ext: Any) -> None:
    if getattr(ext, "_sim_fix_proc_file_enabled_model", None) is None:
        ext._sim_fix_proc_file_enabled_model = ui.SimpleBoolModel(False)
    if not isinstance(getattr(ext, "_sim_fix_proc_file_names", None), list):
        ext._sim_fix_proc_file_names = []
    if getattr(ext, "_sim_fix_proc_file_applied_text", None) is None:
        ext._sim_fix_proc_file_applied_text = ""
    if getattr(ext, "_sim_fix_proc_file_applied_name", None) is None:
        ext._sim_fix_proc_file_applied_name = ""
    _bind_fix_proc_file_enabled_once(ext)


def refresh_fix_proc_file_names(ext: Any) -> List[str]:
    names = list_fix_proc_txt_files()
    ext._sim_fix_proc_file_names = names
    idx = int(getattr(ext, "_sim_fix_proc_file_combo_idx", 0) or 0)
    if names and idx >= len(names):
        ext._sim_fix_proc_file_combo_idx = 0
    elif not names:
        ext._sim_fix_proc_file_combo_idx = 0
    return names


def _combo_selected_index(ext: Any, combo: Any) -> int:
    try:
        return int(combo.model.get_item_value_model().as_int)
    except Exception:
        return int(getattr(ext, "_sim_fix_proc_file_combo_idx", 0) or 0)


def _is_fix_proc_file_enabled(ext: Any) -> bool:
    mdl = getattr(ext, "_sim_fix_proc_file_enabled_model", None)
    if mdl is None:
        return False
    try:
        return bool(mdl.get_value_as_bool())
    except Exception:
        return False


def _update_fix_proc_hud_status_label(ext: Any) -> None:
    lbl = getattr(ext, "_sim_fix_proc_file_status_lbl", None)
    if lbl is None:
        return
    try:
        if not _is_fix_proc_file_enabled(ext):
            lbl.text = "파일사용 OFF"
            return
        applied = str(getattr(ext, "_sim_fix_proc_file_applied_name", "") or "").strip()
        n_lines = len(
            [ln for ln in str(getattr(ext, "_sim_fix_proc_file_applied_text", "") or "").splitlines() if ln.strip()]
        )
        if applied and n_lines > 0:
            lbl.text = f"사용: {applied} ({n_lines}줄)"
        elif applied:
            lbl.text = f"사용: {applied} (내용 없음)"
        else:
            lbl.text = "선택 파일 없음"
    except Exception:
        pass


def read_selected_fix_proc_txt(ext: Any) -> Tuple[str, str]:
    """콤보 선택 항목의 ``(파일명, 본문)``."""
    names = list(getattr(ext, "_sim_fix_proc_file_names", None) or refresh_fix_proc_file_names(ext))
    if not names:
        return "", ""
    combo = getattr(ext, "_sim_fix_proc_file_combo", None)
    if combo is not None:
        idx = _combo_selected_index(ext, combo)
    else:
        idx = int(getattr(ext, "_sim_fix_proc_file_combo_idx", 0) or 0)
    idx = max(0, min(idx, len(names) - 1))
    ext._sim_fix_proc_file_combo_idx = idx
    fname = names[idx]
    path = fix_proc_txt_dir() / fname
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return fname, path.read_text(encoding=enc)
        except Exception:
            continue
    return fname, ""


def sync_fix_proc_file_from_hud(ext: Any) -> bool:
    """파일사용 ON → 선택 txt 즉시 로드. OFF → 파일 스냅샷 비움."""
    ensure_fix_proc_file_hud_models(ext)
    if not _is_fix_proc_file_enabled(ext):
        ext._sim_fix_proc_file_applied_name = ""
        ext._sim_fix_proc_file_applied_text = ""
        _update_fix_proc_hud_status_label(ext)
        return False
    fname, text = read_selected_fix_proc_txt(ext)
    ext._sim_fix_proc_file_applied_name = fname
    ext._sim_fix_proc_file_applied_text = str(text or "")
    _update_fix_proc_hud_status_label(ext)
    ok = bool(fname and str(text or "").strip())
    if ok:
        try:
            import carb

            carb.log_info(
                f"[tbs] fix proc txt 사용: {fname!r} "
                f"({len(ext._sim_fix_proc_file_applied_text)} chars)"
            )
        except Exception:
            pass
    return ok


def _bind_fix_proc_file_enabled_once(ext: Any) -> None:
    if getattr(ext, "_sim_fix_proc_file_enabled_bound", False):
        return
    mdl = getattr(ext, "_sim_fix_proc_file_enabled_model", None)
    if mdl is None:
        return

    def _on_enabled(_m: Any, *_a: Any) -> None:
        sync_fix_proc_file_from_hud(ext)

    try:
        mdl.add_value_changed_fn(_on_enabled)
        ext._sim_fix_proc_file_enabled_bound = True
    except Exception:
        pass


def _bind_fix_proc_file_combo(ext: Any, combo: Any) -> None:
    def _on_combo(_m: Any, *_a: Any) -> None:
        try:
            ext._sim_fix_proc_file_combo_idx = int(_m.get_item_value_model().as_int)
        except Exception:
            pass
        if _is_fix_proc_file_enabled(ext):
            sync_fix_proc_file_from_hud(ext)

    try:
        combo.model.add_item_changed_fn(_on_combo)
    except Exception:
        pass
    ext._sim_fix_proc_file_combo = combo


def fix_proc_txt_text_for_sim_start(ext: Any) -> Optional[str]:
    """파일사용 ON 이면 선택 파일을 디스크에서 다시 읽어 반환."""
    if not _is_fix_proc_file_enabled(ext):
        return None
    sync_fix_proc_file_from_hud(ext)
    text = str(getattr(ext, "_sim_fix_proc_file_applied_text", "") or "").strip()
    if not text:
        return None
    return text


def build_fix_proc_file_ebs_rows(ext: Any, *, lw: int, cb_style: Any) -> None:
    """EBS Viewport HUD 상단 fix txt 행."""
    ensure_fix_proc_file_hud_models(ext)
    names = refresh_fix_proc_file_names(ext)
    combo_w = max(96, 284 - int(lw))

    with ui.HStack(spacing=4, height=26):
        ui.Label("fix txt", width=lw, style={"color": 0xFF9AA4B2, "font_size": 11})
        if names:
            idx = int(getattr(ext, "_sim_fix_proc_file_combo_idx", 0) or 0)
            idx = max(0, min(idx, len(names) - 1))
            combo = ui.ComboBox(idx, *names)
            try:
                combo.width = combo_w
            except Exception:
                pass
            _bind_fix_proc_file_combo(ext, combo)
        else:
            ui.Label(
                "(data/txt/*.txt 없음)",
                width=combo_w,
                style={"color": 0xFF9AA4B2, "font_size": 10},
            )
    with ui.HStack(spacing=4, height=22):
        ui.Label("파일사용", width=lw, style={"color": 0xFF9AA4B2, "font_size": 11})
        ui.CheckBox(model=ext._sim_fix_proc_file_enabled_model, width=20, style=cb_style)
        ext._sim_fix_proc_file_status_lbl = ui.Label(
            "",
            style={"color": 0xFF7EB8DA, "font_size": 10},
        )
    _update_fix_proc_hud_status_label(ext)
    if _is_fix_proc_file_enabled(ext):
        sync_fix_proc_file_from_hud(ext)
