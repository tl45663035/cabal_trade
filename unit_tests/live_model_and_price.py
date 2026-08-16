"""LIVE: the 30-slot row model, and get_price_diff, against the real client.

DRIVES THE GAME. It reads the shop, seeds the model, checks the model against
what is on screen, then walks the Purchase tab for every Core/Set pair.
It BUYS NOTHING and LISTS NOTHING -- every action here is a read or a tab
switch.

Two things are under test and they are independent:

  THE MODEL     seeded from one full walk of all 30 slots, then checked row by
                row against a SECOND, independent read. A divergence means the
                model and the shop disagree about what is in a slot, which is
                the thing that would make a cancel touch the wrong listing.

  GET_PRICE_DIFF  row 1 of each side, per unit, for all five pairs.

Stop it with a file named STOP in the repo root; checked between steps.

    python unit_tests/live_model_and_price.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

# NEVER THE REAL LEDGER.
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "live_model_and_price.db")

STOP = _ROOT / "STOP"

PAIRS = [("Chaos", 4, 3), ("FCH", 8, 7), ("FCHH", 2, 1),
         ("FCU", 6, 5), ("UCU", 10, 9)]


def rule(title):
    print()
    print("=" * 84)
    print(title)
    print("=" * 84)


def main() -> int:
    import trade
    from cabal import calibrate as cab_calibrate, shop as cab_shop
    import get_price_diff as gpd

    print(__doc__)
    trade.PREMIUM_ENABLED = True

    # ---------------------------------------------------------------- setup
    rule("0. open the shop and calibrate")
    if not trade.ensure_shop_ready(verbose=True):
        print("Could not open the Agent Shop.")
        return 2
    # The Register tab: half the calibration anchors are its own furniture.
    if not trade.open_trade_window(verbose=False):
        print("Could not reach the Register tab.")
        return 2
    if not trade.calibrate(verbose=True):
        print("Could not calibrate.")
        return 2

    # ---------------------------------------------------------------- model
    rule("1. THE MODEL: seed it from one full walk")
    started = time.perf_counter()
    seed = trade.shop_listing_pairs(timeout=8.0, verbose=False)
    walk_seconds = time.perf_counter() - started
    if not seed:
        print("The shop could not be walked; the model cannot be seeded.")
        return 1
    covered = max(i for i, _ in seed)
    print(f"  walked {len(seed)} row(s), reaching row {covered} of "
          f"{trade.SHOP_ROW_CAPACITY}, in {walk_seconds:.1f}s")
    if covered < trade.SHOP_ROW_CAPACITY:
        print(f"  NOT seeding: a slot the walk never reached is not the same "
              f"as an empty one, and the model would hand it to the next "
              f"registration.")
        return 1
    trade.SHOP.adopt(seed)
    print(trade.SHOP.describe())

    rule("2. THE MODEL: check it against a SECOND, independent read")
    print("  The seed above is one observation. This is another one taken")
    print("  moments later, compared slot by slot. Anything that disagrees is")
    print("  a divergence -- the condition that would let a cancel touch the")
    print("  wrong listing.\n")
    if STOP.exists():
        print(f"{STOP.name} present - stopping.")
        return 0
    again = trade.shop_listing_pairs(timeout=8.0, verbose=False)
    if not again:
        print("  the second read failed; nothing to compare against.")
        return 1

    checked = diverged = 0
    for index, row in again:
        try:
            trade.SHOP.check(index, row)
            checked += 1
        except Exception as exc:            # noqa: BLE001 - that IS the result
            diverged += 1
            print(f"  DIVERGED row {index}: {exc}")
    print(f"  {checked} row(s) agreed, {diverged} diverged")
    if diverged == 0:
        print("  The model matches the shop on every slot it was asked about.")

    # ------------------------------------------------------------- prices
    rule("3. GET_PRICE_DIFF: row 1 per unit, every pair")
    layout = cab_calibrate.calibrated_layout(verbose=False)
    if layout is None:
        print("  src/cabal could not measure the window.")
        return 1
    print(f"  layout: origin {layout.origin}, scale {layout.scale:.4f}")
    print(f"  {layout.measured_from}\n")

    results = []
    for name, set_slot, core_slot in PAIRS:
        if STOP.exists():
            print(f"{STOP.name} present - stopping.")
            break
        t = time.perf_counter()
        got = gpd.get_price_diff(set_slot, core_slot, in_shop=True,
                                 verbose=False, layout=layout)
        took = time.perf_counter() - t
        results.append((name, set_slot, core_slot, got, took))
        shown = f"{got:,}" if got is not None else "None"
        print(f"  {name:<6} slots {set_slot}-{core_slot}  {took:5.1f}s  "
              f"-> {shown:>12} Alz/unit")

    # ------------------------------------------------------------- tidy up
    rule("4. put the game back")
    trade.open_trade_window(verbose=True)
    trade.leave_shop(verbose=True)

    # -------------------------------------------------------------- report
    rule("RESULT")
    print(f"  MODEL          {checked} slot(s) checked, {diverged} divergence(s)")
    if results:
        ok = [r for r in results if r[3] is not None]
        print(f"  GET_PRICE_DIFF {len(ok)} of {len(results)} pair(s) read, "
              f"mean {sum(r[4] for r in results) / len(results):.1f}s a call")
        print()
        print(f"  {'item':<8}{'slots':<9}{'Alz/unit':>14}{'seconds':>10}")
        for name, a, b, got, took in results:
            shown = f"{got:,}" if got is not None else "None"
            print(f"  {name:<8}{f'{a}-{b}':<9}{shown:>14}{took:>9.1f}s")
        print()
        print("  Sign is Set/unit minus Core. POSITIVE means crafting up pays")
        print("  (the chaos direction); NEGATIVE means converting Sets down to")
        print("  Cores pays by that much per unit.")
    return 0 if diverged == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
