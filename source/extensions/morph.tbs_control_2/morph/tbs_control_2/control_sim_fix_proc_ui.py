"""fix 공정 입력 전용 Kit 창."""

from __future__ import annotations

from typing import Any

import omni.ui as ui


def build_fix_proc_window(ext: Any) -> None:
    """「fix 공정 입력」창 — 멀티라인 텍스트, 디스크 저장 없음."""
    existing = getattr(ext, "_fix_proc_window", None)
    if existing is not None:
        return
    try:
        ws = getattr(ui, "Workspace", None)
        if ws is not None and hasattr(ws, "get_window"):
            old = ws.get_window("fix 공정 입력")
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    try:
                        old.visible = False
                    except Exception:
                        pass
    except Exception:
        pass

    if getattr(ext, "_sim_fix_proc_text_model", None) is None:
        ext._sim_fix_proc_text_model = ui.SimpleStringModel("")

    ext._fix_proc_window = ui.Window("fix 공정 입력", width=480, height=320)
    with ext._fix_proc_window.frame:
        with ui.VStack(spacing=6, height=ui.Fraction(1.0)):
            with ui.Frame(style={"background_color": 0xFF1E2530}, height=ui.Fraction(1.0)):
                with ui.VStack(padding=8, spacing=6, height=ui.Fraction(1.0)):
                    ui.Label(
                        "형식: 이름, OHT→EP(초), EP→OHT(초)  —  한 줄당 LOT_001, LOT_002 … 순서",
                        height=36,
                        style={"color": 0xFFBFE7FF, "font_size": 12},
                        word_wrap=True,
                    )
                    ui.Label(
                        "예: tacny80, 586, 143",
                        height=18,
                        style={"color": 0xFF9AA4B2, "font_size": 11},
                    )
                    ui.StringField(
                        model=ext._sim_fix_proc_text_model,
                        multiline=True,
                        height=ui.Fraction(1.0),
                        style={"font_size": 13},
                    )
