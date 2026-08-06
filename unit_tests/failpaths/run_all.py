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
}
# Closed 2026-08-06: "9 outage replay -- the recovery (clearing a stranded work
# tab) does not exist". It exists now (recover_stranded_work_tab), t9 grew a 9d
# for it, and t25 covers it directly.
#
# Two things were wrong with that entry beyond the missing fix. Its wording
# described the recovery, but the check actually failing was about recording --
# and trade.py had recorded worktab.not_empty since before the entry was
# written. The check could not pass either way: 9c patched
# require_empty_work_tab out and then asserted that it recorded a frame, so it
# was measuring the stub. Same shape as the t6 NameError: a KNOWN_OPEN reason
# written from an assumption rather than read off the suite's output, which
# then survives every green run because nobody re-reads a line that is expected
# to be there.

# The FORENSICS scripts contain NO assertions -- they print what the recorded
# index contains and always exit 0. They cannot fail, so showing them as
# "clean" beside suites that can is a false green: three rows that look like
# passing tests and test nothing.
#
# '6 index forensics' was listed in KNOWN_OPEN with a confident description of
# findings it had supposedly reported. It had reported nothing: it was dying on
# a NameError, exiting 1, and the exit code was read as "has findings". The
# reason was written to fit that assumption instead of being read off the
# suite's output, and it survived a full green run.
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
        # A suite that DIED is not a suite that reported findings, but both
        # exit 1, so KNOWN_OPEN swallowed the difference: a NameError in t6
        # was filed under its known finding and the build stayed green while
        # the suite executed nothing at all. The give-away was in the summary
        # the whole time -- 0.0s -- and it read like just another known one.
        #
        # A crash therefore fails the build no matter what KNOWN_OPEN says.
        # KNOWN_OPEN is a statement about findings the suite REPORTS; it can
        # never be a licence for the suite not to run.
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

    # A suite that was expected to fail and now passes is worth saying out
    # loud: it means a fix landed and the list should shrink.
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





