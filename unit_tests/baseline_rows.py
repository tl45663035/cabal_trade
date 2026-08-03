"""Freeze read_rows' output on every corpus frame, so a rewrite can be proved.

    py unit_tests\\baseline_rows.py save     write baseline_rows.json
    py unit_tests\\baseline_rows.py check    compare the current code against it

The comparison separates three things, because lumping them together made a
successful bug fix look like a regression:

  HARD      price, quantity, action, index, click band -- these decide which
            row gets cancelled and at what price. Any change is a failure.
  NAME      a changed name is only acceptable if it is the old name with junk
            tokens removed (the option-socket icon OCRs as 'an' / 'mm').
  NEW       frames captured since the baseline was written. Not differences.

This complements suite_corpus: that checks frames which carry recorded ground
truth, this one checks EVERY frame, including the ones nothing else asserts on.
"""

import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import trade as m  # noqa: E402

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
BASELINE = HERE / "baseline_rows.json"

# Matches suite_corpus: fixed, not scaled to the machine, because the game and
# the live recording script are normally running alongside this.
JOBS = 16

HARD_FIELDS = ("index", "price", "qty", "action", "change", "top", "bottom")


def read_one(path_name):
    """One frame's rows, as plain data. Runs in a worker process."""
    path = CORPUS / path_name
    try:
        rows = m.read_rows(Image.open(path))
    except Exception:                      # noqa: BLE001 - a frame is not worth a crash
        return path_name, None
    return path_name, [
        {"index": r.index, "name": r.name, "price": r.price, "qty": r.qty,
         "action": r.action, "change": list(r.change),
         "top": r.top, "bottom": r.bottom}
        for r in rows
    ]


def _ignore_sigint():
    """Pool worker initialiser: let the parent own Ctrl+C. See suite_corpus."""
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def snapshot(jobs=JOBS):
    frames = sorted(p.name for p in CORPUS.glob("*.png"))
    started = time.monotonic()
    out = {}
    pool = None
    if jobs == 1:
        results = map(read_one, frames)
    else:
        pool = Pool(processes=jobs, initializer=_ignore_sigint)
        results = pool.imap_unordered(read_one, frames, chunksize=4)
    done = 0
    try:
        for name, rows in results:
            done += 1
            if rows is not None:
                out[name] = rows
            if done % 200 == 0:
                rate = done / max(time.monotonic() - started, 1e-9)
                print(f"  {done}/{len(frames)} frames, {rate:.1f}/s, "
                      f"{(len(frames)-done)/max(rate,1e-9)/60:.1f} min left",
                      flush=True)
    except KeyboardInterrupt:
        print(f"\ninterrupted after {done}/{len(frames)} frames", flush=True)
        raise
    finally:
        # terminate(), not close(), and in a finally: close() waits for every
        # queued frame, which on an interrupt is the rest of the corpus, and
        # sitting after the loop meant an exception orphaned the workers.
        if pool is not None:
            pool.terminate()
            pool.join()
    return out, time.monotonic() - started, len(frames)


def is_junk_removal(before: str, after: str) -> bool:
    """Is `after` just `before` with whole tokens dropped?"""
    want, got = after.split(), before.split()
    i = 0
    for tok in got:
        if i < len(want) and tok == want[i]:
            i += 1
    return i == len(want) and any(t not in want for t in got)


def main():
    mode = "check"
    for arg in sys.argv[1:]:
        if not arg.startswith("-"):
            mode = arg
    jobs = JOBS
    if "--jobs" in sys.argv:
        jobs = max(1, int(sys.argv[sys.argv.index("--jobs") + 1]))

    data, elapsed, n = snapshot(jobs)
    rows_read = sum(len(v) for v in data.values())
    print(f"{n} frames, {rows_read} rows, {elapsed:.1f}s "
          f"({elapsed / max(n, 1):.2f}s per frame, {jobs} workers)")

    if mode == "save":
        BASELINE.write_text(json.dumps(data, indent=1))
        print(f"baseline written to {BASELINE}")
        return 0

    # A fresh machine has no corpus: the script builds one by running. Without
    # this, every baselined frame reads as "unreadable now" and the suite
    # reports ~1000 failures on a checkout that is perfectly fine.
    if n == 0:
        print("no corpus frames on this machine yet - nothing to compare.")
        print("Run the script once to record some, then 'save' a baseline.")
        return 0

    if not BASELINE.exists():
        print("no baseline yet - run with 'save' first")
        return 0 if n else 1

    old = json.loads(BASELINE.read_text())
    hard, name_junk, name_other, shape, new = [], [], [], [], []

    for frame in sorted(set(old) | set(data)):
        a, b = old.get(frame), data.get(frame)
        if a is None:
            new.append(frame)
            continue
        if b is None:
            shape.append(f"{frame}: in baseline, unreadable now")
            continue
        if len(a) != len(b):
            shape.append(f"{frame}: {len(a)} rows -> {len(b)} rows")
            continue
        for ra, rb in zip(a, b):
            for field in HARD_FIELDS:
                if ra[field] != rb[field]:
                    hard.append(f"{frame} row {ra['index']} {field}: "
                                f"{ra[field]!r} -> {rb[field]!r}")
            if ra["name"] != rb["name"]:
                target = name_junk if is_junk_removal(ra["name"], rb["name"]) \
                    else name_other
                target.append(f"{frame} row {ra['index']}: "
                              f"{ra['name']!r} -> {rb['name']!r}")

    print(f"  {'frames in baseline':38} {len(old):6,d}")
    print(f"  {'new since baseline (not differences)':38} {len(new):6,d}")
    print(f"  {'HARD changes':38} {len(hard):6,d}   <- must be 0")
    print(f"  {'row-count changes':38} {len(shape):6,d}   <- must be 0")
    print(f"  {'name changes, not junk removal':38} {len(name_other):6,d}   <- must be 0")
    print(f"  {'name changes, junk removed':38} {len(name_junk):6,d}   (acceptable)")
    for line in (hard + shape + name_other)[:25]:
        print(f"    FAIL {line}")
    for line in name_junk[:5]:
        print(f"    junk {line}")

    if hard or shape or name_other:
        return 1
    print("\nread_rows is unchanged on every baselined frame")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
