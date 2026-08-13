"""LIVE head-to-head: trade.chaos_margin_now against src/get_price_diff.

DRIVES THE REAL GAME. Both implementations click real favourite slots in the
real client and read the real market. Neither buys anything -- both only
search and read -- but the mouse will be busy for several minutes.

Requires the Agent Shop to be open. It switches to the Purchase tab, runs the
comparison, and puts the Register tab back at the end.

Stop it by creating a file named STOP in the repo root; it is checked between
calls, never inside one.

    python unit_tests/get_price_diff_live.py [repeats]
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

# NEVER THE REAL LEDGER. Nothing here sells anything, but trade.py opens the
# sales database on import and this is a benchmark, not a trading run.
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "get_price_diff_live_bench.db")

STOP = _ROOT / "STOP"

# Core/Set pairs from the favourites table. A is the SET (the crafted output),
# B is the CORE (the raw input), so a positive difference is profit per unit.
#
# THE ORDER IS THE TEST. Running one pair five times in a row is not a fair
# measurement: after the first search that item's results are already on
# screen, so every later search finds the name it was looking for whether or
# not the click did anything. offers_match_slot passes trivially and a search
# that never ran is invisible -- which flatters whichever implementation is
# worse at making the search happen.
#
# Sequencing through different items makes a failed search visible: the screen
# still shows the PREVIOUS item, the name does not match, and the retry fires.
# Each pair is also internally fair, because a call searches its Set slot and
# then its Core slot, which are different items.
SEQUENCE = [
    ("Chaos", 4, 3),
    ("FCH", 8, 7),
    ("FCHH", 2, 1),
    ("FCU", 6, 5),
    ("UCU", 10, 9),
]
PAIRS = SEQUENCE


def stopped() -> bool:
    return STOP.exists()


def main(rounds: int = 2) -> int:
    import trade
    from cabal import calibrate, purchase, screen, shop
    import get_price_diff as gpd

    print(__doc__)
    print("=" * 88)

    layout = calibrate.calibrated_layout(verbose=True)
    if layout is None:
        print("\nCould not measure the Trade window. Open the Agent Shop and "
              "try again. Nothing was clicked.")
        return 2

    state = shop.read_state(layout)
    if not state.window_open:
        print("\nThe Agent Shop is not open. Open it and try again. Nothing "
              "was clicked.")
        return 2
    was_register = state.register_tab

    # Both implementations need the Purchase tab and the Low-to-High sort.
    # Done once here so neither pays for it inside a measurement.
    if not shop.open_purchase_tab(layout, verbose=True):
        print("\nCould not reach the Purchase tab. Nothing else was clicked.")
        return 2
    if not purchase.set_sort_low_to_high(layout, verbose=True):
        print("\nCould not confirm the Price: Low to High sort. Refusing.")
        return 2

    print()
    print(f"Running the {len(SEQUENCE)}-item sequence {rounds}x per "
          f"implementation = {len(SEQUENCE) * rounds * 2} calls.")
    print("Order: " + " -> ".join(n for n, _, _ in SEQUENCE))
    print("Hands off the mouse.")
    print("=" * 88)

    results = {name: {"old": [], "new": [], "old_ans": [], "new_ans": []}
               for name, _, _ in SEQUENCE}
    order = []
    try:
        # ONE IMPLEMENTATION AT A TIME, each walking the whole sequence twice.
        #
        # Not alternating OLD/NEW per item: that would hand the second one the
        # first one's results already on screen, which is the very thing the
        # sequencing is here to avoid.
        for label in ("old", "new"):
            for lap in range(rounds):
                for name, set_slot, core_slot in SEQUENCE:
                    if stopped():
                        raise KeyboardInterrupt(f"{STOP.name} is present")
                    if label == "old":
                        trade.CHAOS_SET_SLOT = set_slot
                        trade.CHAOS_CORE_SLOT = core_slot
                        t = time.perf_counter()
                        got = trade.chaos_margin_now(verbose=False)
                        took = time.perf_counter() - t
                    else:
                        t = time.perf_counter()
                        got = gpd.get_price_diff(set_slot, core_slot,
                                                 in_shop=True, verbose=False,
                                                 layout=layout)
                        took = time.perf_counter() - t
                    results[name][label].append(took)
                    results[name][label + "_ans"].append(got)
                    order.append((label, lap + 1, name))
                    shown = f"{got:,}" if got is not None else "None"
                    print(f"  {label.upper():<4} lap {lap + 1}  {name:<6} "
                          f"{took:6.1f}s -> {shown:>14}")
    except KeyboardInterrupt as exc:
        print("")
        print(f"{exc} - stopping.")
    finally:
        if was_register:
            print("")
            print("putting the Register tab back...")
            shop.open_register_tab(layout, verbose=True)

    rows = []
    for name, set_slot, core_slot in SEQUENCE:
        r = results[name]
        if not r["old"] or not r["new"]:
            continue
        rows.append(dict(
            name=name, set_slot=set_slot, core_slot=core_slot,
            old=sum(r["old"]) / len(r["old"]),
            new=sum(r["new"]) / len(r["new"]),
            old_min=min(r["old"]), new_min=min(r["new"]),
            old_ans=r["old_ans"], new_ans=r["new_ans"]))

    if not rows:
        print("\nNo complete measurements.")
        return 1

    print()
    print("SPEED  (wall clock per call, mean over the laps)")
    print("-" * 88)
    print(f"  {'item':<8}{'OLD mean':>10}{'NEW mean':>10}{'OLD best':>10}"
          f"{'NEW best':>10}{'faster':>10}{'saved/call':>13}")
    for r in rows:
        speed = r["old"] / r["new"] if r["new"] else 0
        print(f"  {r['name']:<8}{r['old']:>9.1f}s{r['new']:>9.1f}s"
              f"{r['old_min']:>9.1f}s{r['new_min']:>9.1f}s"
              f"{speed:>9.1f}x{r['old'] - r['new']:>11.1f}s")
    to = sum(r["old"] for r in rows)
    tn = sum(r["new"] for r in rows)
    print("-" * 88)
    print(f"  {'ALL':<8}{to:>9.1f}s{tn:>9.1f}s{'':>10}{'':>10}"
          f"{(to / tn if tn else 0):>9.1f}x{to - tn:>11.1f}s")

    print()
    print("PRICE DIFF  (live market, Alz per unit, Set minus Core)")
    print("-" * 88)
    print(f"  {'item':<8}{'slots':<9}{'OLD (all rows)':>18}"
          f"{'NEW (row 1)':>16}{'delta':>12}{'stable':>9}")
    for r in rows:
        o = [a for a in r["old_ans"] if a is not None]
        n = [a for a in r["new_ans"] if a is not None]
        om = sum(o) // len(o) if o else None
        nm = sum(n) // len(n) if n else None
        delta = (nm - om) if (om is not None and nm is not None) else None
        stable = "yes" if len(set(o)) <= 1 and len(set(n)) <= 1 else "moved"
        print(f"  {r['name']:<8}{f'{r[chr(115)+chr(101)+chr(116)+chr(95)+chr(115)+chr(108)+chr(111)+chr(116)]}-{r[chr(99)+chr(111)+chr(114)+chr(101)+chr(95)+chr(115)+chr(108)+chr(111)+chr(116)]}':<9}"
              f"{(f'{om:,}' if om is not None else 'None'):>18}"
              f"{(f'{nm:,}' if nm is not None else 'None'):>16}"
              f"{(f'{delta:+,}' if delta is not None else '-'):>12}{stable:>9}")

    print()
    print("  OLD scans EVERY row of the Set search and takes the cheapest per")
    print("  unit. NEW takes row 1 by rule. Under a sort by LISTING TOTAL those")
    print("  differ whenever the smallest bundle is not the best value, so a")
    print("  non-zero delta is the rule working, not a fault.")
    print("  'moved' means the market changed between repeats.")
    return 0


if __name__ == "__main__":
    laps = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    raise SystemExit(main(laps))
