"""Run every test suite, time each one, report the total.

    py unit_tests\\run_all.py

Exit code is non-zero if any suite fails. Each suite's own output is echoed,
then a summary table gives per-suite wall time so it is obvious where the
budget goes -- which, on this project, is always OCR.
"""

import os
import re
import tempfile
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
    # Registered 2026-08-15. run_all refuses to run at all while any suite is
    # unlisted, so an unregistered file does not merely go untested -- it stops
    # the whole runner. These five had accumulated unlisted, which is why the
    # guard had been failing silently.
    ("purchase dialog",       HERE / "purchase_dialog_test.py", []),
    ("market floors",         HERE / "market_floor_test.py", []),
    ("plan changes",          HERE / "plan_changes_test.py",  []),
    ("pin replay",            HERE / "pin_replay_test.py",    []),
    ("row model reconcile",   HERE / "reconcile_test.py",     []),
    ("dialog_kind",           HERE / "dialog_kind_test.py",   []),
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
    ("shop_model",            HERE / "shop_model_test.py", []),
    ("idle_cycle",            HERE / "idle_cycle_test.py", []),
    ("live_config",           HERE / "live_config_test.py", []),
    # The --buy pipeline: sold out -> buy Sets -> convert -> list. Mostly about
    # the ORDER the three stages run in and when the next one is allowed to
    # start, since each stage has its own suite already.
    ("restock (--buy)",       HERE / "restock_test.py",      []),
    # The four functions a coverage audit found with no test at all --
    # purchase_confirm (which buy_offer refuses on) and the tooltip readers.
    ("buying/convert gaps",   HERE / "buying_gaps_test.py",  []),
    # The defects a ten-agent review found that the suites above did NOT catch,
    # and the guards that now stop them. Every section drives the real function
    # rather than a stub, because the reason the others missed these was that
    # they could not have failed: they restated the rule under test, derived
    # both sides of a boundary from the function being bounded, or only ever
    # supplied inputs that already satisfied the check.
    ("review fixes",          HERE / "review_fixes_test.py", []),
    # The four regressions introduced on 2026-08-09/10 while fixing other
    # things, and caught by review rather than by any suite above. Each check
    # was verified to FAIL with its fix reverted -- all four mutations caught
    # in a sandbox on 2026-08-10 -- which is the property the rest of this
    # registry mostly lacks: an audit that day found 32 of 43 mutations
    # survived the failpath suites, and 14 of 20 survived the unit suites.
    ("regression fixes",      HERE / "regression_fixes_test.py", []),
    # Sequential replay of what the live script actually recorded: every step
    # must leave the screen in the state the next step assumes, and re-reading
    # a frame must reproduce the value stored beside it. No stubs, so it can
    # see the things the stub suites answer for and therefore cannot test --
    # coordinates, regions, tabs, confidence. One episode per cycle, per row,
    # per Core resupply and per chaos resupply.
    ("sequence replay",       HERE / "sequence_replay_test.py", []),
    # The resupply flow replayed against real screenshots taken while it ran.
    # Its strongest checks are cross-reader: purchase_confirm reads a centred
    # dialog and read_purchase_rows reads the table behind it, and the price
    # they find must agree though they share no region and no code.
    ("flow goldens",          HERE / "flow_goldens_test.py", []),
    # Reverts each of those fixes in memory and re-runs the suites, so a fix
    # with no test behind it is reported rather than assumed. Slow -- it builds
    # trade.py sixteen times -- so it is not in the default run; invoke it
    # directly with `py unit_tests/mutation_check.py` after changing a guard.
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
    # Added 2026-08-09. All four existed and none was ever run by this file --
    # including the two covering the code most recently changed. The docstring
    # said "every test suite" while four sat on disk untouched, so a green run
    # here meant less than anyone reading it believed.
    ("purchase sort control", HERE / "sort_control_test.py", []),
    ("scroll avoidance",      HERE / "bring_into_view_test.py", []),
    ("identical stacks",      HERE / "siblings_test.py",     []),
    ("floors: the amounts",   HERE / "floor_amounts_test.py", []),
    ("tooltip over dialog",   HERE / "tooltip_guard_test.py", []),
    ("mid-cycle resupply",    HERE / "mid_cycle_restock_test.py", []),
    ("ledger accounting",     HERE / "accounting_test.py",    []),
    ("work-tab gate",         HERE / "worktab_gate_test.py",  []),
    ("chaos pair separation", HERE / "chaos_test.py",         []),
    ("chaos pass ordering",   HERE / "chaos_pass_test.py",    []),
    ("chaos per-row floors",  HERE / "chaos_lots_test.py",    []),
    ("bought stock report",   HERE / "stock_report_test.py",  []),
    ("shop sweep / cache",    HERE / "sweep_cache_test.py",  []),
    ("corpus sequences",      HERE / "sequence_test.py",     []),
    ("item price reuse",      HERE / "item_price_reuse_test.py", []),
    ("war lag / server clock", HERE / "warlag_test.py",      []),
    ("log step timings",      HERE / "log_timing_test.py",   []),
]

# A suite that exists but is not listed above is a suite nobody runs.
#
# That is not hypothetical: sort_control, bring_into_view, warlag and
# log_timing were all written, reported as passing, and then never executed by
# the command documented as running everything. This makes the omission
# impossible rather than merely discouraged.
EXCLUDED = {
    # Manual tools and generators, each with its own usage banner.
    "capture_goldens.py", "clean_corpus.py", "find_anchors.py",
    "promote_goldens.py", "probe_flow.py", "baseline_extend.py",
    # Deliberately not in the default run: rebuilds trade.py once per
    # mutation. Invoke directly after changing a guard.
    "mutation_check.py",
    # Invoked as a group by the failpaths runner listed above.
    "run_all.py",
    # Excluded 2026-08-15. Not suites: the first two exercise the standalone
    # src/cabal stack rather than trade.py, and the third is a log-analysis
    # tool that takes a run log as an argument.
    "cabal_stack_test.py", "get_price_diff_test.py", "row_coverage_check.py",
    # Not a suite: imported BY suites to arm every input primitive so a test
    # cannot drive the live client. Run directly it takes a suite path as an
    # argument, which is how the input audit is done.
    "_no_input_guard.py",
}


def _unlisted() -> list[str]:
    listed = {path.name for _label, path, _args in SUITES}
    on_disk = {f.name for f in HERE.glob("*.py")
               if f.name.endswith(("_test.py", "_smoke.py", "_check.py"))
               or f.name.startswith("suite_") or f.name.startswith("baseline_")}
    return sorted(on_disk - listed - EXCLUDED)


# Point the sales ledger at a scratch file BEFORE any suite starts.
#
# The failure-path suites replay the collect path for real, and note_sale()
# writes a row wherever SALES_DB points. Left alone, that is the user's live
# ledger: 1,163 of its 1,168 rows on 2026-08-07 were this suite, and every
# "what did I make today" total had been counting them as income.
#
# Set in os.environ, not on the module, because each suite runs as its own
# subprocess and inherits the environment but not our globals.
_SALES_SCRATCH = Path(tempfile.gettempdir()) / "cabal_test_sales.db"
os.environ["CABAL_SALES_DB"] = str(_SALES_SCRATCH)

# ONE LEDGER PER SUITE once suites run concurrently.
#
# Serially a single scratch file is fine. In parallel it is not: sqlite writers
# contend, and a suite that asserts on its own rows would read another's. The
# per-suite path is derived from the label so a failure names the file that
# holds the evidence.
_SALES_DIR = Path(tempfile.mkdtemp(prefix="cabal_run_all_"))


def _suite_env(label: str) -> dict:
    env = dict(os.environ)
    # SANITISED: labels contain "/" and ":" ("import / globals",
    # "floors: the amounts"), which become nested paths and NTFS alternate
    # data streams rather than filenames.
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "suite"
    env["CABAL_SALES_DB"] = str(_SALES_DIR / f"{safe}.db")
    return env


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cores = 1
    for i, a in enumerate(argv):
        if a.startswith("--cores"):
            # --cores 8 and --cores=8, and `cores=8` because that is what the
            # operator typed.
            raw = a.split("=", 1)[1] if "=" in a else (
                argv[i + 1] if i + 1 < len(argv) else "")
            cores = max(1, int(raw or 1))
        elif a.startswith("cores="):
            cores = max(1, int(a.split("=", 1)[1] or 1))
    results = []
    total_started = time.monotonic()

    # Refuse to report a green run while a suite sits unlisted. A missing
    # suite is indistinguishable from a passing one in the summary below, and
    # that is exactly how four of them went unrun for a day.
    orphans = _unlisted()
    if orphans:
        bar = "=" * 72
        print(bar)
        print("UNLISTED SUITES")
        print(bar)
        print("These exist on disk and are in neither SUITES nor EXCLUDED:")
        for name in orphans:
            print(f"  {name}")
        print("Add each to SUITES, or to EXCLUDED with a reason. "
              "Refusing to report a result while any suite is "
              "unaccounted for.")
        return 1

    def run_one(entry):
        label, path, args = entry
        if not path.exists():
            return (label, None, "MISSING", f"MISSING: {path}")
        started = time.monotonic()
        proc = subprocess.run([sys.executable, str(path), *args],
                              cwd=str(ROOT), env=_suite_env(label),
                              capture_output=(cores > 1), text=True)
        elapsed = time.monotonic() - started
        out = "" if cores == 1 else ((proc.stdout or "") + (proc.stderr or ""))
        return (label, elapsed,
                "pass" if proc.returncode == 0 else "FAIL", out)

    if cores > 1:
        # OUTPUT IS BUFFERED AND REPLAYED IN SUITE ORDER. Interleaved stdout
        # from eight suites is unreadable, and this file exists to make a
        # failure obvious. Threads, not processes: every suite is already its
        # own subprocess, so the pool only waits on them.
        from concurrent.futures import ThreadPoolExecutor
        print(f"Running {len(SUITES)} suite(s) on {cores} core(s); "
              f"each suite's output is replayed below in order.", flush=True)
        with ThreadPoolExecutor(max_workers=cores) as pool:
            done = list(pool.map(run_one, SUITES))
        for label, elapsed, status, out in done:
            print(f"\n{'=' * 72}\n=== {label}\n{'=' * 72}", flush=True)
            if out:
                print(out, end="" if out.endswith("\n") else "\n", flush=True)
            results.append((label, elapsed, status))
    else:
        for entry in SUITES:
            print(f"\n{'=' * 72}\n=== {entry[0]}\n{'=' * 72}", flush=True)
            label, elapsed, status, _out = run_one(entry)
            results.append((label, elapsed, status))

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
