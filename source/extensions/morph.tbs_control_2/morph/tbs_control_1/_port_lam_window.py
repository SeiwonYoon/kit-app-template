"""Port lam_window.py -> tbs_usd_window.py (USD-only, no LAM viewport/CSV/external)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXT_ROOT = ROOT.parent.parent.parent  # .../extensions
LAM = EXT_ROOT / "morph.lam_control" / "morph" / "lam_control" / "lam_window.py"
OUT = ROOT / "tbs_usd_window.py"

SKIP_METHODS = {
    "_open_editor",
    "_open_json_test",
    "_open_csv_sim_play",
    "_sync_csv_viewport_hud",
    "_sync_wafer_foup_viewport_labels_only",
    "_refresh_wafer_labels_after_master_open",
    "_on_force_dedicated_viewport",
    "_on_run_external",
    "_on_pause_external",
    "_on_resume_external",
    "_on_restart_external",
    "_on_stop_external",
    "_on_apply_speed",
    "_on_browse_results",
    "_ensure_csv_sim_window",
    "_ensure_status_panel",
    "_ensure_foup_status_3d",
    "_ensure_device_labels_3d",
    "_register_overlay_toggle_listener",
    "_on_overlay_toggle_changed",
    "_try_autoload_master_on_startup",
    "_schedule_autoload_master_on_startup",
}

REMOVE_IMPORT_LINES = (
    "lam_external_event_runner",
    "lam_json_test_window",
    "lam_sequence_editor",
    "simulation_play",
    "lam_viewport",
    "lam_csv_viewport_hud",
    "lam_viewport_status_panel",
    "lam_viewport_foup_status_3d",
    "lam_viewport_device_labels_3d",
    "lam_viewport_overlay_state",
    "lam_wafer_viewport_labels",
)


def transform(text: str) -> str:
    text = text.replace("lam_data_paths", "tbs_data_paths")
    text = text.replace("from .lam_", "from .tbs_")
    text = re.sub(r"\bLamWindow\b", "TbsUsdWindow", text)
    text = re.sub(r"\bLamViewport\b", "None", text)
    text = re.sub(r"\[LAM/WIN\]", "[TBS/USD]", text)
    text = text.replace('WINDOW_TITLE = "LAM Multi-USD Load"', 'WINDOW_TITLE = "TBS USD Load"')
    text = text.replace("load_automatically = True", "load_automatically = False  # EP autoload via equipment_autoload")
    text = text.replace('default_load_usd_path = "usd/master_1.usd"', 'default_load_usd_path = "usd/test1.usd"')
    lines = []
    for line in text.splitlines():
        if any(x in line for x in REMOVE_IMPORT_LINES):
            continue
        lines.append(line)
    text = "\n".join(lines)
    # drop methods by naive regex (class body methods at indent 4)
    for name in SKIP_METHODS:
        pat = rf"^    def {name}\(.*?(?=^    def |\Z)"
        text = re.sub(pat, "", text, flags=re.MULTILINE | re.DOTALL)
    # remove viewport init block in __init__
    text = text.replace("        self._viewport = None(self._master.context_name)\n", "")
    text = re.sub(
        r"        self._viewport = .*?\n",
        "        self._viewport = None  # TBS: default Kit viewport only\n",
        text,
        count=1,
    )
    # clean show() auto editor / viewport calls
    text = text.replace("                self._open_editor()\n", "")
    text = text.replace("                self._sync_csv_viewport_hud()\n", "")
    text = text.replace("                self._sync_wafer_foup_viewport_labels_only()\n", "")
    text = text.replace("            self._viewport.show()\n", "            pass  # default viewport\n")
    text = text.replace("        view_status = self._viewport.status_text() if self._viewport else \"viewport=N/A\"\n", '        view_status = "default viewport"\n')
    text = text.replace("        if not self._viewport.is_default_visible() and not self._viewport.has_dedicated():\n            self._log(\n                \"※ 화면에 안 보이면 도구 영역의 [LAM Viewport 강제 열기] 를 눌러 주세요.\"\n            )\n", "")
    text = text.replace("            self._refresh_wafer_labels_after_master_open(delay_frames=24)\n", "")
    # remove tools + external collapsible UI blocks (marker-based)
    text = re.sub(
        r"                ui\.Separator\(\)\n\n                # ─── 도구 ─.*?ui\.Separator\(\)\n\n                # ─── 외부 시뮬 결과",
        "                ui.Separator()\n\n                # ─── 진단 (USD) ─",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"                # ─── 진단 \(USD\) ─.*?ui\.Separator\(\)\n\n                # ─── 로그",
        "                ui.Separator()\n\n                # ─── 로그",
        text,
        flags=re.DOTALL,
    )
    # insert diagnose buttons before log if removed too much - add minimal tools
    if "_on_diagnose" in text and "Master 진단" not in text:
        insert = (
            '                cf_tools = ui.CollapsableFrame("진단", collapsed=True, height=0)\n'
            "                with cf_tools:\n"
            "                    with ui.HStack(spacing=4, height=24):\n"
            '                        ui.Button("Master 진단", clicked_fn=self._on_diagnose, width=120)\n'
            '                        ui.Button("Option E 진단", clicked_fn=self._on_diagnose_option_e, width=120)\n'
        )
        text = text.replace("                # ─── 로그", insert + "\n                ui.Separator()\n\n                # ─── 로그")
    return text


def main() -> None:
    src = LAM.read_text(encoding="utf-8")
    OUT.write_text(transform(src), encoding="utf-8")
    print(f"wrote {OUT.name} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
