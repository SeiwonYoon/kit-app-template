from pathlib import Path
import re

LAM = Path(r"c:\Users\ptK\Documents\kit-app-template_mine\source\extensions\morph.lam_control\morph\lam_control\lam_window.py")
OUT = Path(__file__).resolve().parent / "tbs_usd_window.py"
text = LAM.read_text(encoding="utf-8")
text = text.replace("morph.lam_control", "morph.tbs_control_1")
text = text.replace(".lam_data_paths", ".tbs_data_paths")
text = text.replace("from .lam_", "from .tbs_")
text = re.sub(r"\bLamWindow\b", "TbsUsdWindow", text)
text = re.sub(r"\[LAM/WIN\]", "[TBS/USD]", text)
text = text.replace('WINDOW_TITLE = "LAM Multi-USD Load"', 'WINDOW_TITLE = "TBS USD Load"')
text = text.replace("load_automatically = True", "load_automatically = False")
OUT.write_text(text, encoding="utf-8")
print("wrote", OUT.stat().st_size)
