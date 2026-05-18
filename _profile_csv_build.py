"""CSV 파싱 vs build_csv_playback_plan 소요 시간 (Kit 없이)."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXT = ROOT / "source" / "extensions" / "morph.lam_control"
sys.path.insert(0, str(EXT))

from morph.lam_control.simulation_play import (  # noqa: E402
    build_csv_playback_plan,
    load_csv_dwell_timeline,
    preview_csv_playback_schedule,
    read_csv_rows,
    normalize_csv_timeline,
    build_lot_id_to_foup_index,
    rows_to_dwell_records,
    sort_dwells_for_playback,
    set_csv_playback_compact_log,
)

CSV = ROOT / "lam" / "csv" / "eap_tasjr91_sample_v1.csv"

# build_steps_for_event 호출 횟수
_calls = {"n": 0}
_orig = None


def _patch():
    global _orig
    from morph.lam_control import lam_event_sequences as les

    _orig = les.build_steps_for_event

    def _wrapped(*a, **kw):
        _calls["n"] += 1
        return _orig(*a, **kw)

    les.build_steps_for_event = _wrapped
    # simulation_play 가 이미 import 한 참조도 패치
    import morph.lam_control.simulation_play as sp

    sp.build_steps_for_event = _wrapped


def _unpatch():
    if _orig is None:
        return
    from morph.lam_control import lam_event_sequences as les
    import morph.lam_control.simulation_play as sp

    les.build_steps_for_event = _orig
    sp.build_steps_for_event = _orig


def main() -> None:
    _patch()
    try:
        t0 = time.perf_counter()
        rows = read_csv_rows(CSV)
        t_read = time.perf_counter() - t0

        t1 = time.perf_counter()
        raw = normalize_csv_timeline(rows)
        lot = build_lot_id_to_foup_index(raw)
        dwells = sort_dwells_for_playback(rows_to_dwell_records(raw, lot))
        t_dwell = time.perf_counter() - t1

        t2 = time.perf_counter()
        _calls["n"] = 0
        set_csv_playback_compact_log(True)
        _, blocks = build_csv_playback_plan(dwells)
        t_plan_compact = time.perf_counter() - t2
        n_compact = _calls["n"]

        t3 = time.perf_counter()
        _calls["n"] = 0
        set_csv_playback_compact_log(False)
        _, _ = build_csv_playback_plan(dwells)
        t_plan_verbose = time.perf_counter() - t3
        n_verbose = _calls["n"]

        t4 = time.perf_counter()
        _calls["n"] = 0
        set_csv_playback_compact_log(False)
        preview_csv_playback_schedule(str(CSV))
        t_preview = time.perf_counter() - t4
        n_preview = _calls["n"]

        action = [b for b in blocks if b.steps]
        first_action_t = min(b.time_sec for b in action) if action else None
        first_transfer = min(
            (b.time_sec for b in action if b.category == "transfer"),
            default=None,
        )

        print(f"CSV: {CSV.name}  rows={len(rows)}  dwells={len(dwells)}  blocks={len(blocks)}  action={len(action)}")
        print(f"  read_csv_rows:              {t_read*1000:7.1f} ms")
        print(f"  dwell pipeline:             {t_dwell*1000:7.1f} ms")
        print(f"  build_plan (compact):       {t_plan_compact*1000:7.1f} ms  build_steps_for_event x{n_compact}")
        print(f"  build_plan (verbose/log):   {t_plan_verbose*1000:7.1f} ms  build_steps_for_event x{n_verbose}")
        print(f"  preview (== plan+parse):    {t_preview*1000:7.1f} ms  build_steps_for_event x{n_preview}")
        print(f"  first action t={first_action_t}s  first transfer t={first_transfer}s")
        print(f"  Play 1회 체감(미리보기+스레드 빌드): ~{(t_preview + t_plan_compact)*1000:.0f} ms + CSV대기 {first_transfer}s/배속")
    finally:
        _unpatch()


if __name__ == "__main__":
    main()
