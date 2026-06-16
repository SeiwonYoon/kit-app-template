# temp patch script - delete after use
from pathlib import Path

p = Path(__file__).with_name("ebs_control_panel_ui.py")
head = p.read_text(encoding="utf-8").splitlines(keepends=True)[:252]
tail = Path(__file__).with_name("_ebs_panel_tail.py").read_text(encoding="utf-8")
p.write_text("".join(head) + tail, encoding="utf-8")
print("patched", p)
