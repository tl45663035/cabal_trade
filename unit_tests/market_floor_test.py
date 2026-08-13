"""The floor an item starts the run on, before anything has been bought.

DRIVES NOTHING. Pure floor arithmetic.

purchase_cost_basis is `this_run_only`, so on a fresh process it is 0 for
everything -- and an ordinary Core has no catalogue floor either, so its floor
is 0. A floor of 0 is not a floor: the run opens by relisting stock it already
holds at whatever the market happens to be, with nothing underneath it. Goods
bought last night at 500,000 go out this morning at 400,000 with every other
guard satisfied.

The market price read once at startup stands in until a real purchase replaces
it.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "market_floor_test.db")

sys.argv = ["market_floor_test"]
import trade  # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def rule(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


def seed(name, unit):
    trade._MARKET_FLOORS[trade._floor_key(name)] = unit


def clear():
    trade._MARKET_FLOORS.clear()


CORE = "Force Core(High)"
VIP = "Siena's Bracelet"          # matches the 'siena' catalogue floor

rule("without a market read, a Core has no floor at all")

clear()
floor, why = trade.listing_floor(CORE)
check(floor == 0,
      f"a Core starts on 0 with nothing read ({floor:,}) -- this is the hole")
check(trade.market_floor(CORE) == 0, "and no market floor is recorded")

rule("the market read becomes the floor")

seed(CORE, 250_000)
floor, why = trade.listing_floor(CORE)
check(floor == 250_000, f"the Core now floors at the market price ({floor:,})")
check("market" in why,
      f"and the log says which rule bound: {why!r}")

rule("a real purchase takes over from the stand-in")

# purchase_cost_basis is consulted first; only when it is 0 does the market
# stand in. Simulated by asking listing_floor with a cost basis present.
real_cost = 300_000
saved = trade.purchase_cost_basis
try:
    trade.purchase_cost_basis = lambda name, this_run_only=True: (
        real_cost if trade._floor_key(name) == trade._floor_key(CORE) else 0)
    floor, why = trade.listing_floor(CORE)
    check(floor == real_cost,
          f"once stock is bought, what was PAID wins ({floor:,}) over the "
          f"250,000 market stand-in")
    check("bought" in why, f"and says so: {why!r}")

    # Even when the purchase was CHEAPER than the market read.
    real_cost = 200_000
    floor, why = trade.listing_floor(CORE)
    check(floor == 200_000,
          "a cheaper purchase still wins -- the stand-in exists only while "
          "there is no cost basis, it is not a second floor to clear")
finally:
    trade.purchase_cost_basis = saved

rule("a VIP's catalogue floor is never weakened by the market")

clear()
catalogue = trade.item_price_floor(VIP)
check(catalogue > 0, f"the VIP has a catalogue floor ({catalogue:,})")

seed(VIP, 1_000)                  # a crashed or misread market
floor, why = trade.listing_floor(VIP)
check(floor == catalogue,
      f"a 1,000 market read does NOT drag the VIP down; floor stays "
      f"{floor:,}")
check(floor >= catalogue, "the absolute floor always wins")

seed(VIP, catalogue * 2)          # a market above the catalogue floor
floor, why = trade.listing_floor(VIP)
check(floor >= catalogue,
      "and a market above the catalogue floor never lowers it either")

rule("an unread item is left alone, not floored at zero by implication")

clear()
seed(CORE, 250_000)
check(trade.market_floor("Upgrade Core (Ultimate)") == 0,
      "an item the read could not price has no stand-in -- an unread price "
      "is not evidence, and inventing one would be a guess")

rule("the seeder is once per process")

import inspect  # noqa: E402
src = inspect.getsource(trade.seed_market_floors)
check("_MARKET_FLOORS_READ" in src,
      "seed_market_floors guards on a once-per-process flag")
check("_row_one" in src,
      "it takes ROW 1 of each search -- the operator's rule")
check("MIN_PLAUSIBLE_PRICE" in src,
      "and refuses an implausible price rather than flooring on a clipped read")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
