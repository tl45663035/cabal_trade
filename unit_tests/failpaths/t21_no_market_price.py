"""What to list at when the market suggests nothing.

The incident, 2026-08-05 08:0x, on a live 30-row relist:

    ########## 3/30: row 3 - "Craftsman's SIGMetal Headpiece (BL) + 15" ####
    [relist 1/2] row 3: ... at 85,000,000 Alz
    Loaded: qty '1 /1' -> 1/1, suggested [0, 0]
    Overriding to 10,000,000,000 Alz - no market price; using the fallback
    Registered (1,1) qty 1 at 10,000,000,000 Alz each

An item the owner had listed at 85,000,000 was relisted at 10,000,000,000,
where it cannot sell. The panel read `suggested [0, 0]` because the item is
unique enough that nothing comparable is listed.

FALLBACK_PRICE is right for a FRESH listing -- there is no previous price and
parking it high beats guessing low. On a RELIST there is one, and an item with
no comparable listing is precisely the one whose owner-chosen price is the best
information available. Reaching past it for a constant threw that away.

choose_price is pure, so this drives it directly: no harness, no stubs, and
every combination of previous price, floor and market reading.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from harness import check, section, summary  # noqa: E402

import trade  # noqa: E402

VIP_FLOOR = next(f for t, _, f in trade.ITEM_PRICE_FLOORS if t == "vip")


# ===========================================================================
section("the incident: no market price, but a previous one exists")

price, why = trade.choose_price(0, floor_price=85_000_000)
check("keeps the previous price, not the 10B fallback",
      price == 85_000_000,
      f"got {price:,} -- 10,000,000,000 is where the live item went, and it "
      f"cannot sell there")
check("says what it did", "keeping the previous" in why, f"{why!r}")
check("does not claim to have used the fallback",
      "fallback" not in why, f"{why!r}")


# ===========================================================================
section("a FRESH listing still parks high -- there is nothing to keep")

price, why = trade.choose_price(0, floor_price=None)
check("no previous price: uses the fallback", price == trade.FALLBACK_PRICE,
      f"got {price:,}")
check("...and says so", "fallback" in why, f"{why!r}")

price, why = trade.choose_price(0, floor_price=0)
check("previous price of 0 counts as none", price == trade.FALLBACK_PRICE,
      f"got {price:,}")


# ===========================================================================
section("a previous price too small to believe is not kept")

# MIN_PLAUSIBLE_PRICE exists because a misread table price must not become the
# new listing price. That applies here more than anywhere: this path runs when
# there is no market reading to cross-check against.
for bad in (1, 10, trade.MIN_PLAUSIBLE_PRICE - 1):
    price, why = trade.choose_price(0, floor_price=bad)
    check(f"previous price {bad} is rejected as a misread",
          price == trade.FALLBACK_PRICE,
          f"got {price:,} -- keeping it would list the item for pennies")

price, why = trade.choose_price(0, floor_price=trade.MIN_PLAUSIBLE_PRICE)
check(f"exactly MIN_PLAUSIBLE_PRICE is kept",
      price == trade.MIN_PLAUSIBLE_PRICE, f"got {price:,}")


# ===========================================================================
section("the absolute floor still binds over a kept price")

# The floor is a MINIMUM and outranks everything, including the price the item
# was previously listed at. A VIP whose market vanished must not drop back to
# an old, lower price.
price, why = trade.choose_price(0, floor_price=50_000_000,
                                absolute_floor=VIP_FLOOR)
check("a previous price BELOW the floor is raised to the floor",
      price == VIP_FLOOR,
      f"got {price:,}, floor is {VIP_FLOOR:,} -- the floor is absolute and "
      f"this path must not be a way round it")

price, why = trade.choose_price(0, floor_price=200_000_000,
                                absolute_floor=VIP_FLOOR)
check("a previous price ABOVE the floor is kept as-is",
      price == 200_000_000, f"got {price:,}")

price, why = trade.choose_price(0, floor_price=None,
                                absolute_floor=VIP_FLOOR)
check("fallback still respects the floor",
      price == max(trade.FALLBACK_PRICE, VIP_FLOOR), f"got {price:,}")


# ===========================================================================
section("a real market reading is unaffected")

# The whole point is that this only changes the no-market path. If a market
# price exists it wins -- but no longer to any depth: RELATIVE_PRICE_FLOOR
# clamps a relist to 95% of what the item is currently listed at. This suite
# used to assert the opposite ("a large drop is reported, never overridden"),
# which was the rule before the ratchet was added, and then asserted 90%, which
# was the rule before the operator tightened it to 5%.
#
# The figures below are written out rather than computed from
# RELATIVE_PRICE_FLOOR on purpose. Deriving both sides from the constant is how
# a test comes to agree with whatever the code does -- an audit of this tree
# found the row-capacity boundary doing exactly that, and the function it
# guarded could return a constant 1 with every suite still green.
price, why = trade.choose_price(437_569, floor_price=85_000_000)
check("a market 99% below the listed price is clamped, not obeyed",
      price == 80_750_000,
      f"got {price:,} -- 437,569 against a listed 85,000,000 is far likelier "
      f"to be a clipped read than a real market")
check("...and the reason names the ratchet",
      "5% below the listed" in why, f"{why!r}")

# An ordinary market move is still taken verbatim.
price, why = trade.choose_price(81_000_000, floor_price=85_000_000)
check("a market within 5% is used unchanged", price == 81_000_000 and why == "",
      f"got {price:,} {why!r}")

# The edge the constant actually defines, from both sides. 95% of 85,000,000 is
# exactly 80,750,000: at it, the market is obeyed; a single Alz under, the
# ratchet takes over and the answer is the same 80,750,000 either way -- which
# is what makes this the boundary rather than a cliff.
price, why = trade.choose_price(80_750_000, floor_price=85_000_000)
check("exactly 5% below is not a drop worth clamping",
      price == 80_750_000 and why == "", f"got {price:,} {why!r}")
price, why = trade.choose_price(80_749_999, floor_price=85_000_000)
check("one Alz further down IS clamped", price == 80_750_000 and why != "",
      f"got {price:,} {why!r}")

# A drop that used to be allowed under the 10% rule is now refused. Stated
# explicitly because it is the behaviour change the operator asked for.
price, why = trade.choose_price(78_000_000, floor_price=85_000_000)
check("an 8% drop was permitted at 10% and is clamped at 5%",
      price == 80_750_000 and why != "", f"got {price:,} {why!r}")

price, why = trade.choose_price(50_000, floor_price=85_000_000,
                                absolute_floor=VIP_FLOOR)
check("market below an absolute floor is still raised to it",
      price == VIP_FLOOR, f"got {price:,}")


# ===========================================================================
section("--floor still refuses rather than substituting")

raised = False
try:
    trade.choose_price(1_000, price_floor=5_000, floor_price=85_000_000)
except trade.Aborted:
    raised = True
check("an explicit --floor aborts on a low market", raised,
      "--floor refuses outright; it must not quietly fall back to the "
      "previous price either")


raise SystemExit(summary())
