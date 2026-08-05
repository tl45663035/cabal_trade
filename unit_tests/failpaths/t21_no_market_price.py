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
# price exists it wins, exactly as before.
price, why = trade.choose_price(437_569, floor_price=85_000_000)
check("market price beats the previous price", price == 437_569,
      f"got {price:,} -- taking the lowest current price is the rule, and a "
      f"large drop is reported, never overridden")
check("no explanation needed when the market was used", why == "", f"{why!r}")

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
