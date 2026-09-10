import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from harness import check, section, summary

import trade

VIP_FLOOR = next(f for t, _, f in trade.ITEM_PRICE_FLOORS if t == "vip")


section("the incident: no market price, but a previous one exists")

price, why = trade.choose_price(0, floor_price=85_000_000)
check("keeps the previous price, not the 10B fallback",
      price == 85_000_000,
      f"got {price:,} -- 10,000,000,000 is where the live item went, and it "
      f"cannot sell there")
check("says what it did", "keeping the previous" in why, f"{why!r}")
check("does not claim to have used the fallback",
      "fallback" not in why, f"{why!r}")


section("a FRESH listing still parks high -- there is nothing to keep")

price, why = trade.choose_price(0, floor_price=None)
check("no previous price: uses the fallback", price == trade.FALLBACK_PRICE,
      f"got {price:,}")
check("...and says so", "fallback" in why, f"{why!r}")

price, why = trade.choose_price(0, floor_price=0)
check("previous price of 0 counts as none", price == trade.FALLBACK_PRICE,
      f"got {price:,}")


section("a previous price too small to believe is not kept")

for bad in (1, 10, trade.MIN_PLAUSIBLE_PRICE - 1):
    price, why = trade.choose_price(0, floor_price=bad)
    check(f"previous price {bad} is rejected as a misread",
          price == trade.FALLBACK_PRICE,
          f"got {price:,} -- keeping it would list the item for pennies")

price, why = trade.choose_price(0, floor_price=trade.MIN_PLAUSIBLE_PRICE)
check(f"exactly MIN_PLAUSIBLE_PRICE is kept",
      price == trade.MIN_PLAUSIBLE_PRICE, f"got {price:,}")


section("the absolute floor still binds over a kept price")

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


section("a real market reading is unaffected")

check(f"the ratchet is the operator's 1% ({trade.RELATIVE_PRICE_FLOOR})",
      trade.RELATIVE_PRICE_FLOOR == 0.99,
      f"{trade.RELATIVE_PRICE_FLOOR} -- if this was retuned deliberately, "
      f"update the literals in this section together with it: they are "
      f"{int(trade.RELATIVE_PRICE_FLOOR * 100)}% of 85,000,000 and the Alz "
      f"either side of it. They are written out rather than derived so this "
      f"suite cannot silently agree with whatever the code does.")

price, why = trade.choose_price(437_569, floor_price=85_000_000)
check("a market 99% below the listed price is clamped, not obeyed",
      price == 84_150_000,
      f"got {price:,} -- 437,569 against a listed 85,000,000 is far likelier "
      f"to be a clipped read than a real market")
check("...and the reason names the ratchet",
      "1% below the listed" in why, f"{why!r}")

price, why = trade.choose_price(84_500_000, floor_price=85_000_000)
check("a market within 1% is used unchanged", price == 84_500_000 and why == "",
      f"got {price:,} {why!r}")

price, why = trade.choose_price(84_150_000, floor_price=85_000_000)
check("exactly 1% below is not a drop worth clamping",
      price == 84_150_000 and why == "", f"got {price:,} {why!r}")
price, why = trade.choose_price(84_149_999, floor_price=85_000_000)
check("one Alz further down IS clamped", price == 84_150_000 and why != "",
      f"got {price:,} {why!r}")

price, why = trade.choose_price(78_000_000, floor_price=85_000_000)
check("an 8% drop was permitted at 10% and is clamped at 1%",
      price == 84_150_000 and why != "", f"got {price:,} {why!r}")
price, why = trade.choose_price(81_000_000, floor_price=85_000_000)
check("a 4.7% drop was permitted at 5% and is clamped at 1%",
      price == 84_150_000 and why != "", f"got {price:,} {why!r}")

price, why = trade.choose_price(50_000, floor_price=85_000_000,
                                absolute_floor=VIP_FLOOR)
check("market below an absolute floor is still raised to it",
      price == VIP_FLOOR, f"got {price:,}")


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
