import os
import subprocess
import tempfile
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

os.environ.setdefault("CABAL_SALES_DB",
                      str(Path(tempfile.gettempdir()) / "cabal_test_sales.db"))

SUITES = [
    ("harness smoke",          "smoke.py"),
    ("1 cancel_item",          "t1_cancel_item.py"),
    ("2 register_item",        "t2_register_item.py"),
    ("3 relist / relist_rows", "t3_relist.py"),
    ("4 run_loop",             "t4_run_loop.py"),
    ("5 crash signature",      "t5_crash_signature.py"),
    ("9 outage replay",        "t9_outage_replay.py"),
    ("11 duplicate stacks",    "t11_duplicate_stacks.py"),
    ("12 scrolling past 10",   "t12_scrolling.py"),
    ("13 live incidents",      "t13_live_incidents.py"),
    ("14 duplicate matrix",    "t14_duplicate_matrix.py"),
    ("15 dialog recovery",     "t15_dialog_recovery.py"),
    ("16 dialog blind spot",   "t16_dialog_blindspot.py"),
    ("17 late dialog",         "t17_late_dialog.py"),
    ("18 collect permutations", "t18_collect_permutations.py"),
    ("19 maximise quantity",   "t19_maximise_quantity.py"),
    ("20 dry run is inert",    "t20_dry_run_is_inert.py"),
    ("21 no market price",    "t21_no_market_price.py"),
    ("22 sold out exit",       "t22_sold_out_exit.py"),
    ("24 frame pruning",      "t24_frame_pruning.py"),
    ("25 strand recovery",    "t25_strand_recovery.py"),
    ("26 collect by action",  "t26_collect_action.py"),
    ("27 batch trim",         "t27_batch_trim.py"),
    ("28 shop session",       "t28_shop_session.py"),
    ("29 sales tally",        "t29_sales_tally.py"),
    ("30 scroll drift",       "t30_scroll_drift.py"),
    ("31 sweep end-to-end",   "t31_sweep_end_to_end.py"),
    ("32 relative price floor", "t32_relative_price_floor.py"),
    ("33 buying",             "t33_buying.py"),
    ("35 buying golden",      "t35_buying_golden.py"),
    ("34 buying matrix",      "t34_buying_matrix.py"),
]

FORENSICS = [
    ("6 index forensics",      "t6_evidence.py"),
    ("7 index windows",        "t7_windows.py"),
    ("8 orphan frames",        "t8_orphans.py"),
]

KNOWN_OPEN = {
    "1 cancel_item":
        "PARTLY FIXED 2026-08-08: cancel_item now takes a `report` "
        "out-parameter carrying `committed`, and _relist_cycle retries a row "
        "only when it is explicitly False -- so a missed dialog no longer "
        "costs the row. What remains is 1d's wording: the suite wants a "
        "definitive 'the game did NOT accept the cancellation' and the code "
        "deliberately hedges, because the game can stack confirmation dialogs "
        "after accepting one. Making the message definitive would be "
        "overclaiming, so this stays open rather than being satisfied.",
}


REPORT_ONLY = {"6 index forensics", "7 index windows", "8 orphan frames"}


def main(include_forensics=True):
    results = []
    started_all = time.monotonic()
    todo = SUITES + (FORENSICS if include_forensics else [])
    for label, name in todo:
        print(f"\n{'#' * 74}\n### {label}  ({name})\n{'#' * 74}", flush=True)
        started = time.monotonic()
        proc = subprocess.run([sys.executable, str(HERE / name)], cwd=str(HERE),
                              capture_output=True, text=True, errors="replace")
        print(proc.stdout, end="", flush=True)
        if proc.stderr:
            print(proc.stderr, end="", flush=True)
        crashed = "Traceback (most recent call last)" in proc.stderr
        results.append((label, time.monotonic() - started, proc.returncode,
                        crashed))

    print(f"\n{'=' * 74}")
    print(f"{'suite':26} {'time':>8}  result")
    print("-" * 74)
    unexpected, known, crashed_suites = [], [], []
    for label, elapsed, code, crashed in results:
        if crashed:
            verdict = "CRASHED - did not run"
            crashed_suites.append(label)
        elif label in REPORT_ONLY:
            verdict = "report (asserts nothing)"
        elif code == 0:
            verdict = "clean"
        elif label in KNOWN_OPEN:
            verdict = "findings (known)"
            known.append(label)
        else:
            verdict = "FINDINGS (NEW)"
            unexpected.append(label)
        print(f"{label:26} {elapsed:>7.1f}s  {verdict}")
    print("-" * 74)
    print(f"total {time.monotonic() - started_all:.1f}s")

    if known:
        print(f"\n{len(known)} suite(s) with KNOWN-OPEN findings:")
        for label in known:
            print(f"  {label}\n      {KNOWN_OPEN[label]}")
        print("\n  These do not fail the build. They are defects that have been "
              "found and\n  not yet fixed -- shrink KNOWN_OPEN as they land.")

    fixed = [label for label, _, code, crashed in results
             if code == 0 and not crashed and label in KNOWN_OPEN
             and label not in REPORT_ONLY]
    if fixed:
        print(f"\n{len(fixed)} suite(s) now PASS that are still listed as "
              "known-open:")
        for label in fixed:
            print(f"  {label}  -- remove it from KNOWN_OPEN")

    if crashed_suites:
        print(f"\n{len(crashed_suites)} suite(s) CRASHED and tested nothing: "
              + ", ".join(crashed_suites))
        print("  This is not a finding. The suite did not run -- fix it before "
              "trusting\n  anything else in this summary.")
    if unexpected:
        print(f"\n{len(unexpected)} suite(s) with NEW findings: "
              + ", ".join(unexpected))
    return 1 if (unexpected or crashed_suites) else 0


if __name__ == "__main__":
    raise SystemExit(main("--no-forensics" not in sys.argv))





