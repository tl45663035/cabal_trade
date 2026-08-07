"""A relist may not fall more than 10% below what the item is listed at now.

Before this, the only brake on a falling price was a per-item entry in
ITEM_PRICE_FLOORS. Everything else took "the lowest current price, whatever it
is" literally:

    listed 100,000,000, market 50,000,000  ->  listed at 50,000,000
    listed 100,000,000, market        999  ->  listed at        999

MIN_PLAUSIBLE_PRICE guards the price read off the TABLE, not the market
suggestion, and SUSPECT_PRICE_FRACTION only printed a note before listing at
the market price anyway. So an unfloored item was one clipped OCR read away
from being given away.

It is a RATCHET, not a hard floor, and that distinction is the whole design: a
genuine crash is still followed at 10% per relist, converging on the market
over several cycles. A real drop costs a few cycles slightly above the market;
a misread costs 10% instead of everything.
"""
from harness import check, section, summary

import trade

PREV = 200_000
FLOOR = trade.RELATIVE_PRICE_FLOOR


def price(market, previous=PREV, absolute=0):
    return trade.choose_price(market, 0, previous or None, absolute)


# ===========================================================================
section("the boundary")

check(f"RELATIVE_PRICE_FLOOR is {FLOOR}", abs(FLOOR - 0.90) < 1e-9, f"{FLOOR}")

got, why = price(180_000)
check("exactly 90% is allowed through untouched", got == 180_000 and not why,
      f"{got:,} {why!r}")

got, why = price(179_999)
check("a hair under is clamped to the floor", got == 180_000,
      f"{got:,} -- the user's example: listed 200k, floor 180k")
check("...and says which bound applied", "10% below the listed" in why, why)

got, _ = price(150_000)
check("a 25% drop is clamped", got == 180_000, f"{got:,}")

got, _ = price(999)
check("a clipped misread is clamped, not obeyed", got == 180_000,
      f"{got:,} -- 999 was listed verbatim before this")

got, why = price(250_000)
check("a RISING market is followed all the way up",
      got == 250_000 and not why,
      f"{got:,} -- the ratchet is one-directional by design")


# ===========================================================================
section("rounding never lands under the intended fraction")

for previous in (200_000, 105_999_999, 54_797_776, 1_001, 333_333, 7):
    got, _ = price(1, previous=previous)
    if previous < trade.MIN_PLAUSIBLE_PRICE:
        check(f"previous {previous:,} is too small to set a floor",
              got == 1,
              f"{got:,} -- an untrustworthy previous price must not become "
              f"the floor, or one bad read poisons the next")
        continue
    check(f"previous {previous:,} floors at >= 90%",
          got >= previous * FLOOR,
          f"{got:,} vs {previous * FLOOR:,.1f}")


# ===========================================================================
section("it composes with the absolute per-item floors")

got, why = price(50_000_000, previous=120_000_000, absolute=104_000_000)
check("the HIGHER of the two bounds wins", got == 108_000_000,
      f"{got:,} -- 10% of 120,000,000 is 108,000,000, above the 104,000,000 "
      f"floor, so the ratchet binds")
check("...and the reason names the ratchet, not the floor",
      "10% below the listed" in why, why)

got, why = price(100_000_000, previous=105_999_999, absolute=104_000_000)
check("the absolute floor wins when it is higher", got == 104_000_000,
      f"{got:,} -- 10% of 105,999,999 is 95,399,999, below the floor")
check("...and the reason names the floor",
      "floor for this item" in why, why)

# The standing requirement: a floored item can never go under, whatever the
# previous price was.
for previous in (104_000_001, 200_000_000, 1_000_000):
    got, _ = price(1_000, previous=previous, absolute=104_000_000)
    check(f"a VIP listed at {previous:,} never goes below its floor",
          got >= 104_000_000, f"{got:,}")


# ===========================================================================
section("the cases that had no previous price are unchanged")

got, why = price(150_000, previous=0)
check("a fresh listing takes the market", got == 150_000 and not why,
      f"{got:,} {why!r}")

got, why = price(0, previous=PREV)
check("no market reading still keeps the previous price", got == PREV,
      f"{got:,} {why!r}")
check("...and says so", "keeping the previous" in why, why)

got, _ = price(0, previous=0)
check("no market and no previous still parks at the fallback",
      got == trade.FALLBACK_PRICE, f"{got:,}")


# ===========================================================================
section("the ratchet converges on a genuine crash")

listed, steps = 200_000, []
for _ in range(20):
    listed, why = trade.choose_price(100_000, 0, listed, 0)
    steps.append(listed)
    if not why:
        break
check("a real crash is followed, not blocked", steps[-1] == 100_000,
      f"{steps} -- a hard floor would have stopped at 180,000 forever")
check("...taking several cycles, not one", 4 <= len(steps) <= 12,
      f"{len(steps)} cycles: {steps}")
check("...and never rising on the way down",
      all(b <= a for a, b in zip(steps, steps[1:])), f"{steps}")


raise SystemExit(summary())
