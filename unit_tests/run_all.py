"""Run every test suite, time each one, report the total.

    py unit_tests\\run_all.py

Exit code is non-zero if any suite fails. Each suite's own output is echoed,
then a summary table gives per-suite wall time so it is obvious where the
budget goes -- which, on this project, is always OCR.
"""

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Every suite lives beside this file. Nothing outside the project tree is
# referenced, so the whole directory can be copied to another machine and run
# as-is -- which was not true while half of these sat in a scratchpad.
SUITES = [
    ("import / globals",      HERE / "import_smoke.py",      []),
    ("record guard",          HERE / "record_guard_test.py", []),
    ("pure suite",            HERE / "suite_pure.py",        []),
    ("floors: both items",    HERE / "floor_siena_test.py",  []),
    ("floors: epic boosters", HERE / "floor_booster_test.py", []),
    ("floors: name fuzz",     HERE / "floor_fuzz_test.py",   []),
    ("floors: price paths",   HERE / "floor_paths_test.py",  []),
    # These three replay what the script really saw, from baseline_rows.json,
    # so they cost about a second each and never start Tesseract. They are the
    # only suites here that assert PROPERTIES rather than recorded values --
    # an audit found 91.8% of the older assertions bit-identically circular
    # and none independent, which is how a reader that was wrong when a frame
    # was recorded produced a green suite.
    ("row invariants",        HERE / "invariants_test.py",   []),
    ("row identity",          HERE / "identity_test.py",     []),
    ("floors on real screens", HERE / "floor_live_test.py",  []),
    ("floor catalogue",       HERE / "floor_catalogue_test.py", []),
    ("layout replay",         HERE / "layout_replay_test.py", []),
    # The vendor grid. Mostly refusals: this is the only surface in the file
    # where a plain click spends items with no confirmation, so the tests that
    # matter assert that nothing is clicked when anything is unverified.
    ("convert_cores",         HERE / "convert_cores_test.py", []),
    # The only coverage that drives the STATE MACHINE rather than the readers.
    # It stubs the click/capture layer and runs cancel_item, register_item,
    # relist_rows and run_loop through their failure paths for real -- the code
    # that decides what happens after something goes wrong, which nothing else
    # here touches. Four seconds, and it caught four defects the corpus suite
    # structurally cannot see, because a recorded frame can only show a state
    # the script actually reached.
    ("failure paths",         HERE / "failpaths" / "run_all.py", []),
    ("corpus suite",          HERE / "suite_corpus.py",      []),
    ("read_rows baseline",    HERE / "baseline_rows.py",     ["check"]),
]


def main():
    results = []
    total_started = time.monotonic()

    for label, path, args in SUITES:
        print(f"\n{'=' * 72}\n=== {label}\n{'=' * 72}", flush=True)
        if not path.exists():
            print(f"MISSING: {path}")
            results.append((label, None, "MISSING"))
            continue
        started = time.monotonic()
        proc = subprocess.run([sys.executable, str(path), *args],
                              cwd=str(ROOT))
        elapsed = time.monotonic() - started
        results.append((label, elapsed, "pass" if proc.returncode == 0 else "FAIL"))

    total = time.monotonic() - total_started
    print(f"\n{'=' * 72}")
    print(f"{'suite':26} {'time':>10}  {'share':>6}  result")
    print("-" * 72)
    for label, elapsed, status in results:
        if elapsed is None:
            print(f"{label:26} {'-':>10}  {'-':>6}  {status}")
            continue
        print(f"{label:26} {elapsed:>9.1f}s  {elapsed/total*100:>5.1f}%  {status}")
    print("-" * 72)
    print(f"{'TOTAL':26} {total:>9.1f}s  ({total/60:.1f} min)")

    failed = [r for r in results if r[2] != "pass"]
    if failed:
        print(f"\n{len(failed)} suite(s) did not pass: "
              + ", ".join(r[0] for r in failed))
        return 1
    print("\nall suites passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
