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

rule("a Core is floored by what its SET costs, not by what Cores fetch")

# The operator's rule, 2026-08-14: "If I buy set to convert to cores, next
# launch of script will get the set price as floor for the core." The money
# goes out on Sets -- the restock buys Sets, converts them down, lists the
# Cores -- so the Set's price per piece is what a Core cost to put up.
#
# Tonight's actual reads, from logs/run_2026-08-14_233738.log.
READS = {
    "Force Core(Highest)": 210_000,   "Force Core Set (Highest)": 209_756,
    "Chaos Core": 694_017,            "Chaos Core Set": 705_000,
    "Force Core (Ultimate)": 498_887, "Force Core Set (Ultimate)": 434_560,
    "Force Core(High)": 200_000,      "Force Core Set (High)": 203_955,
    "Upgrade Core (Ultimate)": 462_999,
    "Upgrade Core Set (Ultimate)": 436_000,
}


def seed_pairs():
    """What seed_market_floors leaves behind, Set-over-Core rule applied."""
    clear()
    for n, v in READS.items():
        seed(n, v)
    for slot in sorted(trade.FAVOURITE_SLOTS):
        if slot in trade.CHAOS_SLOTS:
            continue
        set_slot = trade.favourite_set_slot(slot)
        if set_slot is None:
            continue
        unit = trade.market_floor(trade.FAVOURITE_SLOTS[set_slot])
        if unit:
            seed(trade.FAVOURITE_SLOTS[slot], unit)
    # Chaos, inverted: the SET takes the CORE's price.
    core_unit = trade.market_floor(
        trade.FAVOURITE_SLOTS[trade.CHAOS_CORE_SLOT])
    if core_unit:
        seed(trade.FAVOURITE_SLOTS[trade.CHAOS_SET_SLOT], core_unit)


seed_pairs()
check(trade.market_floor("Force Core (Ultimate)") == 434_560,
      f"FCU floors at its Set's 434,560, not the 498,887 Cores were fetching "
      f"(got {trade.market_floor('Force Core (Ultimate)'):,})")
check(trade.market_floor("Upgrade Core (Ultimate)") == 436_000,
      "UCU floors at its Set's 436,000, not 462,999")

# The case that motivated it: stock bought at ~428,571 must keep selling
# through a dip, not freeze above its own cost.
check(trade.market_floor("Force Core (Ultimate)") < 498_887,
      "a position bought at ~428,571 is no longer floored ABOVE what it cost, "
      "which is what stopped it selling at a profitable 460,000")

# The Set keeps its own price -- only Cores are re-floored.
check(trade.market_floor("Force Core Set (Ultimate)") == 434_560,
      "the Set itself is unchanged")

# CHAOS IS INVERTED, AND SO IS ITS FLOOR. It buys Chaos Cores, crafts them up
# and sells Chaos Core SETS -- so the Set, the thing being sold, is floored at
# what its Cores cost. The operator's rule: "Use chaos core as price floor for
# chaos set which is what I'm selling."
check(trade.market_floor("Chaos Core Set") == 694_017,
      f"the chaos SET floors at the 694,017 its Cores cost, not the 705,000 "
      f"Sets were fetching (got {trade.market_floor('Chaos Core Set'):,})")
check(trade.market_floor("Chaos Core") == 694_017,
      "and the chaos Core keeps its own read -- it is the raw material, not "
      "the product")

# THE RULE IN ONE LINE: whatever is being SOLD is floored at what was BOUGHT
# to make it. Normal pairs sell the Core and buy the Set; chaos sells the Set
# and buys the Core.
check(trade.market_floor("Force Core (Ultimate)")
      == trade.market_floor("Force Core Set (Ultimate)"),
      "a Core and its Set carry the same floor -- the Set's price")
check(trade.market_floor("Chaos Core Set")
      == trade.market_floor("Chaos Core"),
      "and a chaos Set carries its Core's price, the mirror of that")

# A VIP has no Set slot at all, so nothing here can touch its floor.
clear()
seed(VIP, 1_000)
floor, _why = trade.listing_floor(VIP)
check(floor == trade.item_price_floor(VIP),
      "a VIP has no Set pairing, so the catalogue floor still rules it")

rule("the floor resolves for a name that carries its pack count")

# THE BUG THIS CATCHES. Floors are seeded from catalogue names ("Chaos Core
# Set"), but every lookup that matters happens against a BOARD name, and the
# board carries the count -- a compressed chaos bundle reads "Chaos Core Set
# X 250". _floor_key folded that marker into the key, so the floor resolved to
# 0 for exactly the listings it was meant to protect.
clear()
seed("Chaos Core Set", 685_000)
seed("Force Core (Ultimate)", 434_560)

for name, want in (("Chaos Core Set", 685_000),
                   ("Chaos Core Set X 250", 685_000),
                   ("Chaos Core Set X 133", 685_000),
                   ("Force Core (Ultimate)", 434_560),
                   ("Force Core (Ultimate) X 250", 434_560)):
    check(trade.market_floor(name) == want,
          f"{name!r} resolves to {want:,} (got "
          f"{trade.market_floor(name):,}) -- a compressed bundle is how chaos "
          f"actually lists, so a 0 here is the floor not applying at all")

check(trade.market_floor("Nothing Priced X 10") == 0,
      "an item the read never saw is still 0, marker or not")

# The pack size travels the same way and must not regress with it.
trade._MARKET_PACKS[trade._market_key("Chaos Core Set")] = 133
check(trade.market_pack("Chaos Core Set X 250") == 133,
      "market_pack strips the marker too, so the row gate sizes correctly "
      "against a board name")

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
