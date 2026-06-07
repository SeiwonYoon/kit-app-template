"""One-shot: copy LAM modules into tbs_control_1 with tbs_* naming. Run from repo root."""
from __future__ import annotations

import re
from pathlib import Path

LAM = Path(__file__).resolve().parents[2].parent / "morph.lam_control" / "morph" / "lam_control"
TBS = Path(__file__).resolve().parent

# source filename -> target filename
MAP = {
    "lam_usd_path.py": "tbs_usd_path.py",
    "lam_types.py": "tbs_types.py",
    "lam_instance_registry.py": "tbs_instance_registry.py",
    "lam_asset_diagnostics.py": "tbs_asset_diagnostics.py",
    "lam_master_stage.py": "tbs_master_stage.py",
    "lam_multi_usd_loader.py": "tbs_multi_usd_loader.py",
    "lam_composition_discovery.py": "tbs_composition_discovery.py",
    "lam_extract_from_master.py": "tbs_extract_from_master.py",
    "lam_bake_omnigraph.py": "tbs_bake_omnigraph.py",
    "lam_instance_runtime.py": "tbs_instance_runtime.py",
    "lam_attribute_reauthor.py": "tbs_attribute_reauthor.py",
    "lam_runtime_evaluator.py": "tbs_runtime_evaluator.py",
    "lam_playback_scheduler.py": "tbs_playback_scheduler.py",
    "lam_id_resolver.py": "tbs_id_resolver.py",
    "lam_hide_helper.py": "tbs_hide_helper.py",
    "lam_offset_correction.py": "tbs_offset_correction.py",
    "lam_master_timeline_play.py": "tbs_master_timeline_play.py",
    "lam_translate_animation.py": "tbs_lam_translate_animation.py",
    "lam_rotate_animation.py": "tbs_lam_rotate_animation.py",
    "lam_sequence_engine.py": "tbs_lam_sequence_engine.py",
    "lam_sequence_editor.py": "tbs_lam_sequence_editor.py",
    "lam_viewport.py": "tbs_viewport.py",
}


def transform(text: str) -> str:
    text = text.replace("morph.lam_control", "morph.tbs_control_1")
    text = text.replace(".lam_data_paths", ".tbs_data_paths")
    text = text.replace("from .lam_", "from .tbs_")
    text = text.replace("from . import lam_translate_animation", "from . import tbs_lam_translate_animation")
    text = text.replace("from . import lam_rotate_animation", "from . import tbs_lam_rotate_animation")
    text = text.replace("import lam_translate_animation", "import tbs_lam_translate_animation")
    text = text.replace("import lam_rotate_animation", "import tbs_lam_rotate_animation")
    text = text.replace("lam_translate_animation.", "tbs_lam_translate_animation.")
    text = text.replace("lam_rotate_animation.", "tbs_lam_rotate_animation.")
    text = re.sub(r"\blam_usd_path\b", "tbs_usd_path", text)
    text = re.sub(r"\blam_types\b", "tbs_types", text)
    text = re.sub(r"\blam_wafer_viewport_labels\b", "tbs_wafer_viewport_labels", text)
    text = re.sub(r"\bLamSequenceRunner\b", "TbsLamSequenceRunner", text)
    text = re.sub(r"\bLamHideController\b", "TbsHideController", text)
    text = re.sub(r"\bLamSequenceEditor\b", "TbsLamSequenceEditor", text)
    text = re.sub(r"\[LAM/", "[TBS/", text)
    text = re.sub(r"\[LAM\]", "[TBS]", text)
    text = re.sub(r"\bLAM_FIXED_FPS\b", "TBS_FIXED_FPS", text)
    # keep alias for ported code referencing LAM_FIXED_FPS in strings only
    if "LAM_FIXED_FPS" not in text and "TBS_FIXED_FPS" in text:
        pass
    text = text.replace("WINDOW_TITLE = \"LAM Sequence Editor\"", 'WINDOW_TITLE = "TBS Sequence Editor"')
    text = text.replace("lam_event_sequences", "tbs_event_sequences_unused")
    text = text.replace("lam_json_test_window", "tbs_json_test_unused")
    text = text.replace("lam_external_event_runner", "tbs_external_unused")
    text = text.replace("simulation_play", "tbs_sim_play_stubs")
    text = text.replace("lam_viewport", "tbs_viewport_unused")
    return text


def main() -> None:
    for src_name, dst_name in MAP.items():
        src = LAM / src_name
        dst = TBS / dst_name
        if not src.is_file():
            raise SystemExit(f"missing source: {src}")
        body = transform(src.read_text(encoding="utf-8"))
        dst.write_text(body, encoding="utf-8")
        print(f"wrote {dst.name}")


if __name__ == "__main__":
    main()
