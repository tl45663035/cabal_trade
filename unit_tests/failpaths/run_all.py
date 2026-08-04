"""Run every failure-path suite and summarise.

    py run_all.py

A FAIL here is a FINDING, not a broken test: each check states what the code
should do, so a failure is a place where trade.py does something else.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

SUITES = [
    ("harness smoke",          "smoke.py"),
    ("1 cancel_item",          "t1_cancel_item.py"),
    ("2 register_item",        "t2_register_item.py"),
    ("3 relist / relist_rows", "t3_relist.py"),
    ("4 run_loop",             "t4_run_loop.py"),
    ("5 crash signature",      "t5_crash_signature.py"),
    ("9 outage replay",        "t9_outage_replay.py"),
]

FORENSICS = [
    ("6 index forensics",      "t6_evidence.py"),
    ("7 index windows",        "t7_windows.py"),
    ("8 orphan frames",        "t8_orphans.py"),
]

# Suites with findings that are KNOWN OPEN, so a new one is distinguishable
# from the backlog.
#
# The alternative designs are both worse. Gating on every finding leaves this
# permanently red, and a suite that is always red stops being read. Not gating
# at all hides real defects behind a green tick. So: these five are reported
# loudly on every run and do not fail the build; anything else does.
#
# Shrink this list as the defects are fixed. A suite dropping OFF it is the
# signal that the work landed.
KNOWN_OPEN = {
    "1 cancel_item":
        "cancel_item returns one False for 'nothing happened' and 'committed "
        "but unverified'; the recorded committed flag means only that the "
        "click was sent",
    "2 register_item":
        "the identity cross-check is skipped entirely when the panel's qty_max "
        "reads None, which is the case its own docstring calls common -- so it "
        "fails OPEN after the cancel is irreversible",
    "4 run_loop":
        "an empty action list returns True, so a cycle that did nothing counts "
        "as a success and resets the consecutive-failure breaker",
    "9 outage replay":
        "the replayed outage still ends with the run stopped; the cause is "
        "recorded now, but the recovery (clearing a stranded work tab) does "
        "not exist",
    "6 index forensics":
        "historic corpus entries carry context from older record() signatures "
        "and 16 frames have no index line -- evidence about the past, not a "
        "defect in the current code",
}


def main(include_forensics=True):
    results = []
    started_all = time.monotonic()
    todo = SUITES + (FORENSICS if include_forensics else [])
    for label, name in todo:
        print(f"\n{'#' * 74}\n### {label}  ({name})\n{'#' * 74}", flush=True)
        started = time.monotonic()
        proc = subprocess.run([sys.executable, str(HERE / name)], cwd=str(HERE))
        results.append((label, time.monotonic() - started, proc.returncode))

    print(f"\n{'=' * 74}")
    print(f"{'suite':26} {'time':>8}  result")
    print("-" * 74)
    unexpected, known = [], []
    for label, elapsed, code in results:
        if code == 0:
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

    # A suite that was expected to fail and now passes is worth saying out
    # loud: it means a fix landed and the list should shrink.
    fixed = [label for label, _, code in results
             if code == 0 and label in KNOWN_OPEN]
    if fixed:
        print(f"\n{len(fixed)} suite(s) now PASS that are still listed as "
              "known-open:")
        for label in fixed:
            print(f"  {label}  -- remove it from KNOWN_OPEN")

    if unexpected:
        print(f"\n{len(unexpected)} suite(s) with NEW findings: "
              + ", ".join(unexpected))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--no-forensics" not in sys.argv))
