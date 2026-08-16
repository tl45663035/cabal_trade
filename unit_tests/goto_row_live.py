"""LIVE: does scrolling N-1 notches actually put absolute row N at the top?

DRIVES THE GAME. It scrolls the Register table and reads it. It clicks
nothing in the table, buys nothing and lists nothing.

THE TEST. One full walk of all 30 slots establishes ground truth -- what is
really in each row. Then goto_row(N) is asked to place a spread of rows at
screen position 1, and what it reads back is compared against that truth.

A pass means row access can stop searching: instead of walking the shop and
matching by identity, the caller computes a notch count and reads ONE band.

It also measures the alternative the operator described -- stepping down one
notch per row instead of returning to the top -- because a batch that walks
rows in order pays 0+1+2+...+9 notches the first way and 9 the second.

    python unit_tests/goto_row_live.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "goto_row_live.db")

STOP = _ROOT / "STOP"


def rule(title):
    print()
    print("=" * 84)
    print(title)
    print("=" * 84)


def main() -> int:
    import trade
    print(__doc__)
    trade.PREMIUM_ENABLED = True

    rule("0. open the shop, calibrate, calibrate the wheel")
    if not trade.ensure_shop_ready(verbose=True):
        print("Could not open the Agent Shop.")
        return 2
    if not trade.open_trade_window(verbose=False):
        print("Could not reach the Register tab.")
        return 2
    if not trade.calibrate(verbose=False):
        print("Could not calibrate.")
        return 2
    print(f"  layout: {trade.LAYOUT.origin}, scale {trade.LAYOUT.scale:.3f}")
    per_notch = trade.calibrate_scroll(verbose=True)
    print(f"  rows per notch: {per_notch}")
    if not per_notch:
        print("  the wheel could not be calibrated against the table.")
        return 1
    print(f"  ROW_INDEX_LIMIT: {trade.ROW_INDEX_LIMIT}")

    rule("1. ground truth: one full walk of all 30 slots")
    started = time.perf_counter()
    pairs = trade.shop_listing_pairs(timeout=8.0, verbose=False)
    walk_seconds = time.perf_counter() - started
    if not pairs:
        print("  the shop could not be walked.")
        return 1
    truth = {index: row for index, row in pairs}
    print(f"  walked {len(truth)} row(s) in {walk_seconds:.1f}s "
          f"(this is the cost goto_row is meant to replace)")
    for i in sorted(truth)[:6]:
        print(f"    row {i:>2}: {truth[i].name!r}")
    print("    ...")

    rule("2. goto_row against that truth")
    # A spread: the top, a middle, the limit, and the boundary beyond it.
    wanted = [1, 2, 3, 7, 12, 20]
    wanted = [n for n in wanted if n in truth]
    checks = passes = 0
    timings = []
    for n in wanted:
        if STOP.exists():
            print(f"  {STOP.name} present - stopping.")
            break
        t = time.perf_counter()
        row = trade.goto_row(n, verbose=False)
        took = time.perf_counter() - t
        timings.append(took)
        checks += 1
        want = truth[n].name
        got = row.name if row else None
        ok = row is not None and trade._canonical(got) == trade._canonical(want)
        passes += ok
        print(f"  {'OK  ' if ok else 'FAIL'} row {n:>2} in {took:5.1f}s   "
              f"read {got!r}")
        if not ok:
            print(f"        expected {want!r}")

    rule("3. the boundary")
    beyond = trade.ROW_INDEX_LIMIT + 1
    print(f"  goto_row({beyond}) should refuse -- the view clamps before it "
          f"can reach the top:")
    refused = trade.goto_row(beyond, verbose=True) is None
    print(f"  {'OK  ' if refused else 'FAIL'} refused: {refused}")
    checks += 1
    passes += refused

    rule("4. stepping down one notch at a time")
    print("  What a batch walking rows in order would actually do: reach row 1,")
    print("  then step ONE notch per row instead of returning to the top.\n")
    step_ok = step_checks = 0
    started = time.perf_counter()
    row = trade.goto_row(1, verbose=False)
    centre = ((trade.TRADE_REGION[0] + trade.TRADE_REGION[2]) // 2,
              (trade.TRADE_REGION[1] + trade.TRADE_REGION[3]) // 2)
    for n in range(1, 9):
        if STOP.exists():
            break
        if n > 1:
            trade.scroll_wheel(centre[0], centre[1], -1)
            trade.park_cursor(settle=trade.TOOLTIP_CLEAR_SECONDS)
            row = trade.read_top_row()
        want = truth.get(n)
        got = row.name if row else None
        ok = (row is not None and want is not None
              and trade._canonical(got) == trade._canonical(want.name))
        step_checks += 1
        step_ok += ok
        print(f"  {'OK  ' if ok else 'FAIL'} step to row {n:>2}: {got!r}")
    step_seconds = time.perf_counter() - started
    print(f"\n  8 rows reached in {step_seconds:.1f}s "
          f"({step_seconds / 8:.1f}s a row)")

    rule("5. put the game back")
    trade.leave_shop(verbose=True)

    rule("RESULT")
    print(f"  goto_row      {passes}/{checks} correct")
    if timings:
        print(f"                {sum(timings) / len(timings):.1f}s a row from "
              f"the top")
    print(f"  stepping      {step_ok}/{step_checks} correct, "
          f"{step_seconds / max(1, step_checks):.1f}s a row")
    print(f"  full walk     {walk_seconds:.1f}s for all {len(truth)} rows "
          f"({walk_seconds / max(1, len(truth)):.1f}s a row) -- what this "
          f"replaces")
    return 0 if (passes == checks and step_ok == step_checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
