
import json
import sys
import time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from baseline_rows import BASELINE, CORPUS, read_one, _ignore_sigint

DEFAULT_JOBS = 4


def main() -> int:
    jobs = DEFAULT_JOBS
    if "--jobs" in sys.argv:
        jobs = max(1, int(sys.argv[sys.argv.index("--jobs") + 1]))

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    on_disk = sorted(p.name for p in CORPUS.glob("run_*.png"))
    todo = [name for name in on_disk if name not in baseline]

    print(f"baseline holds {len(baseline):,} frame(s); "
          f"{len(on_disk):,} on disk; {len(todo):,} to add")
    if not todo:
        print("nothing to do")
        return 0

    started = time.monotonic()
    added = unreadable = 0
    pool = Pool(processes=jobs, initializer=_ignore_sigint)
    try:
        for done, (name, rows) in enumerate(
                pool.imap_unordered(read_one, todo, chunksize=2), 1):
            if rows is None:
                unreadable += 1
            else:
                baseline[name] = rows
                added += 1
            if done % 100 == 0:
                rate = done / max(time.monotonic() - started, 1e-9)
                print(f"  {done}/{len(todo)}  {rate:.1f}/s  "
                      f"{(len(todo)-done)/max(rate,1e-9)/60:.1f} min left",
                      flush=True)
    finally:
        pool.terminate()
        pool.join()

    BASELINE.write_text(json.dumps(baseline, indent=1))
    elapsed = time.monotonic() - started
    print(f"added {added:,} frame(s), {unreadable} unreadable, "
          f"{elapsed:.0f}s on {jobs} worker(s)")
    print(f"baseline now holds {len(baseline):,} frame(s), "
          f"{sum(len(v) for v in baseline.values()):,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
