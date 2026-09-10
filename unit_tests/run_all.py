
import os
import re
import tempfile
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

SUITES = [
    ("import / globals",      HERE / "import_smoke.py",      []),
    ("record guard",          HERE / "record_guard_test.py", []),
    ("pure suite",            HERE / "suite_pure.py",        []),
    ("floors: both items",    HERE / "floor_siena_test.py",  []),
    ("floors: epic boosters", HERE / "floor_booster_test.py", []),
    ("floors: name fuzz",     HERE / "floor_fuzz_test.py",   []),
    ("floors: price paths",   HERE / "floor_paths_test.py",  []),
    ("row invariants",        HERE / "invariants_test.py",   []),
    ("row identity",          HERE / "identity_test.py",     []),
    ("floors on real screens", HERE / "floor_live_test.py",  []),
    ("floor catalogue",       HERE / "floor_catalogue_test.py", []),
    ("layout replay",         HERE / "layout_replay_test.py", []),
    ("convert_cores",         HERE / "convert_cores_test.py", []),
    ("shop_model",            HERE / "shop_model_test.py", []),
    ("idle_cycle",            HERE / "idle_cycle_test.py", []),
    ("live_config",           HERE / "live_config_test.py", []),
    ("restock (--buy)",       HERE / "restock_test.py",      []),
    ("buying/convert gaps",   HERE / "buying_gaps_test.py",  []),
    ("review fixes",          HERE / "review_fixes_test.py", []),
    ("regression fixes",      HERE / "regression_fixes_test.py", []),
    ("sequence replay",       HERE / "sequence_replay_test.py", []),
    ("flow goldens",          HERE / "flow_goldens_test.py", []),
    ("failure paths",         HERE / "failpaths" / "run_all.py", []),
    ("corpus suite",          HERE / "suite_corpus.py",      []),
    ("read_rows baseline",    HERE / "baseline_rows.py",     ["check"]),
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

EXCLUDED = {
    "capture_goldens.py", "clean_corpus.py", "find_anchors.py",
    "promote_goldens.py", "probe_flow.py", "baseline_extend.py",
    "mutation_check.py",
    "run_all.py",
    "_no_input_guard.py",
}


def _unlisted() -> list[str]:
    listed = {path.name for _label, path, _args in SUITES}
    on_disk = {f.name for f in HERE.glob("*.py")
               if f.name.endswith(("_test.py", "_smoke.py", "_check.py"))
               or f.name.startswith("suite_") or f.name.startswith("baseline_")}
    return sorted(on_disk - listed - EXCLUDED)


_SALES_SCRATCH = Path(tempfile.gettempdir()) / "cabal_test_sales.db"
os.environ["CABAL_SALES_DB"] = str(_SALES_SCRATCH)

_SALES_DIR = Path(tempfile.mkdtemp(prefix="cabal_run_all_"))


def _suite_env(label: str) -> dict:
    env = dict(os.environ)
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label).strip("_") or "suite"
    env["CABAL_SALES_DB"] = str(_SALES_DIR / f"{safe}.db")
    return env


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cores = 1
    for i, a in enumerate(argv):
        if a.startswith("--cores"):
            raw = a.split("=", 1)[1] if "=" in a else (
                argv[i + 1] if i + 1 < len(argv) else "")
            cores = max(1, int(raw or 1))
        elif a.startswith("cores="):
            cores = max(1, int(a.split("=", 1)[1] or 1))
    results = []
    total_started = time.monotonic()

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
