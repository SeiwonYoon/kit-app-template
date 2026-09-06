"""임시 디버그 — 실행 후 삭제해도 됨."""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

root = Path(__file__).resolve().parent
pkg = types.ModuleType("morph")
pkg2 = types.ModuleType("morph.tbs_control_2")
sys.modules["morph"] = pkg
sys.modules["morph.tbs_control_2"] = pkg2
pkg2.__path__ = [str(root)]


def load(n: str):
    spec = importlib.util.spec_from_file_location(f"morph.tbs_control_2.{n}", root / f"{n}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"morph.tbs_control_2.{n}"] = m
    setattr(pkg2, n, m)
    assert spec.loader is not None
    spec.loader.exec_module(m)
    return m


for n in [
    "sim_lot_fix_proc",
    "sequence_renewal",
    "sim_sequence_json",
    "sim_sequence_duration",
    "control_sim_prerun_playback",
    "prerun_plan_ssot",
    "prerun_plan_adapt",
]:
    load(n)

ssot = sys.modules["morph.tbs_control_2.prerun_plan_ssot"]
adapt = sys.modules["morph.tbs_control_2.prerun_plan_adapt"]

cfg = ssot.PrerunPlanConfig(
    lot_count=6,
    ep_count=2,
    proc_oht_to_ep=32.5,
    proc_oht_to_inout=32.5,
    proc_inout_to_bp=32.5,
    proc_bp_to_ep=32.5,
    proc_remove=32.5,
    proc_foup=39.9,
    anim_from_json=True,
)
plan = ssot.build_prerun_plan(cfg)
print(ssot.format_plan_timetable(plan))
print("\n=== EP2 lifecycle ===")
for p in plan.processes:
    if p.port == "EP2" or p.to_port == "EP2" or p.from_port == "EP2":
        print(
            f"{p.t_start:7.1f}-{p.t_wall_end!s:>7} {p.kind:<14} {p.lot_id} "
            f"{p.from_port}->{p.to_port} anim={p.t_anim_start}-{p.t_anim_end}"
        )

print("\n=== keyframes (EP focus) ===")
for kf in plan.port_keyframes:
    e1, e2 = kf.ports.get("EP1", ""), kf.ports.get("EP2", "")
    if e1 or e2 or "EP" in kf.note or "remove" in kf.note.lower():
        print(
            f"{kf.t:7.1f} {kf.note:35s} EP1={e1 or '-':8s} EP2={e2 or '-':8s} "
            f"BP1={kf.ports.get('BP1') or '-'} BP2={kf.ports.get('BP2') or '-'}"
        )

# Check: any BP_TO_EP to EP2 before REMOVE of previous EP2 lot
print("\n=== order check BP_TO_EP vs REMOVE on EP2 ===")
ep2_events = []
for p in plan.processes:
    if p.kind == ssot.KIND_REMOVE and p.from_port == "EP2":
        ep2_events.append((p.t_start, "REMOVE", p.lot_id, p.t_anim_end))
    if p.kind == ssot.KIND_BP_TO_EP and p.to_port == "EP2":
        ep2_events.append((p.t_start, "BP_TO_EP", p.lot_id, p.t_anim_start))
    if p.kind == ssot.KIND_OHT_TO_EP and p.to_port == "EP2":
        ep2_events.append((p.t_start, "OHT_TO_EP", p.lot_id, p.t_anim_end))
ep2_events.sort()
for e in ep2_events:
    print(e)

res = adapt.prerun_plan_to_timeline(screen=1, plan=plan)
refs = [
    it
    for it in res.items
    if it.kind == "event"
    and isinstance(it.payload, dict)
    and it.payload.get("seq") == "PORT_OCC_REFRESH"
]
print(f"\nREFRESH count={len(refs)}")
for it in refs:
    if float(it.t) < 50 or (100 < float(it.t) < 130) or (200 < float(it.t) < 230):
        occ = it.payload.get("ports_occupancy") or {}
        print(
            f"  t={it.t:.1f} EP1={occ.get('EP1') or '-'} EP2={occ.get('EP2') or '-'} "
            f"note={it.payload.get('note')}"
        )
