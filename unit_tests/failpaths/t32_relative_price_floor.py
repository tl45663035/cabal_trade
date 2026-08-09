"""A relist may not fall more than RELATIVE_PRICE_FLOOR below the listed price.

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
genuine crash is still followed a step per relist, converging on the market
over several cycles. A real drop costs a few cycles slightly above the market;
a misread costs one step instead of everything.
"""
from harness import check, section, summary

import math
import trade

PREV = 200_000
FLOOR = trade.RELATIVE_PRICE_FLOOR


def price(market, previous=PREV, absolute=0):
    return trade.choose_price(market, 0, previous or None, absolute)


# The bound the ratchet actually imposes, derived rather than typed. Every
# expectation below is expressed through this, so retuning
# RELATIVE_PRICE_FLOOR moves the suite with the code instead of breaking it.
def ratchet(listed):
    """The lowest a relist may go, given what it is listed at now."""
    pct = int(trade.RELATIVE_PRICE_FLOOR * 100)
    return -(-listed * pct // 100)


DROP = 100 - int(trade.RELATIVE_PRICE_FLOOR * 100)
FLOOR_200K = ratchet(200_000)


# ===========================================================================
section("the boundary")

# The VALUE is an operator setting, so it is not pinned -- what must hold is
# that it is a sane ratchet: below 1 (or it forbids every drop) and well above
# 0 (or it forbids nothing). Tightened 0.90 -> 0.95 on 2026-08-07 after a VIP
# fell 9.47% in one relist, which the old bound allowed.
check(f"RELATIVE_PRICE_FLOOR is a ratchet, not a no-op ({FLOOR})",
      0.5 < FLOOR < 1.0, f"{FLOOR}")
check("...and the drop it permits is a whole number of percent",
      abs(FLOOR * 100 - round(FLOOR * 100)) < 1e-9,
      f"{FLOOR} -- the guard arithmetic uses int(FLOOR * 100)")

got, why = price(FLOOR_200K)
check(f"exactly {int(trade.RELATIVE_PRICE_FLOOR * 100)}% is allowed through untouched", got == FLOOR_200K and not why,
      f"{got:,} {why!r}")

got, why = price(179_999)
check("a hair under is clamped to the floor", got == FLOOR_200K,
      f"{got:,} -- the user's example: listed 200k, floor 180k")
check("...and says which bound applied", f"{DROP}% below the listed" in why, why)

got, _ = price(150_000)
check("a 25% drop is clamped", got == FLOOR_200K, f"{got:,}")

got, _ = price(999)
check("a clipped misread is clamped, not obeyed", got == FLOOR_200K,
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
check("the HIGHER of the two bounds wins", got == ratchet(120_000_000),
      f"{got:,} -- the ratchet on 120,000,000 is {ratchet(120_000_000):,}, above the 104,000,000 "
      f"floor, so the ratchet binds")
check("...and the reason names the ratchet, not the floor",
      f"{DROP}% below the listed" in why, why)

# Derived, not typed. The point of this case is a previous price whose
# RATCHET lands below the absolute floor, and 105,999,999 only did that while
# the ratchet was 5%: at 1% its bound is 104,940,000, above the floor, so the
# case silently stopped testing what it names. Sit it halfway between the
# floor and the highest previous price whose ratchet still clears it, and it
# holds at any rate.
_VIP = 104_000_000
_prev_below = int(_VIP + (_VIP / FLOOR - _VIP) * 0.5)
assert ratchet(_prev_below) < _VIP < _prev_below, (
    f"the scenario must put the ratchet BELOW the floor: "
    f"ratchet({_prev_below:,}) = {ratchet(_prev_below):,} vs {_VIP:,}")
got, why = price(100_000_000, previous=_prev_below, absolute=_VIP)
check("the absolute floor wins when it is higher", got == _VIP,
      f"{got:,} -- the ratchet on {_prev_below:,} is {ratchet(_prev_below):,}, below the floor")
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

# The step budget is derived too. A 5% ratchet halves in 14 cycles and a 1%
# ratchet takes 69, so a hardcoded range(20) does not test a slower ratchet --
# it just fails to reach the market and reports a convergence bug that is not
# there.
_want = math.ceil(math.log(0.5) / math.log(trade.RELATIVE_PRICE_FLOOR))
listed, steps = 200_000, []
for _ in range(_want + 5):
    listed, why = trade.choose_price(100_000, 0, listed, 0)
    steps.append(listed)
    if not why:
        break
check("a real crash is followed, not blocked", steps[-1] == 100_000,
      f"{steps} -- a hard floor would have stopped at 180,000 forever")
# Derived from the ratchet rate, not hardcoded: halving the step doubles the
# cycles, so a fixed bound fails when RELATIVE_PRICE_FLOOR is retuned -- for a
# reason that has nothing to do with correctness. What must hold is that it
# converges in the number of steps the rate implies, and not in one.
check("...taking several cycles, not one", _want <= len(steps) <= _want + 3,
      f"{len(steps)} cycles: {steps}")
check("...and never rising on the way down",
      all(b <= a for a, b in zip(steps, steps[1:])), f"{steps}")


raise SystemExit(summary())
