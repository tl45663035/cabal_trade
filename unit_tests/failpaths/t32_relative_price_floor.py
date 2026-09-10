from harness import check, section, summary

import math
import trade

PREV = 200_000
FLOOR = trade.RELATIVE_PRICE_FLOOR


def price(market, previous=PREV, absolute=0):
    return trade.choose_price(market, 0, previous or None, absolute)


def ratchet(listed):
    pct = int(trade.RELATIVE_PRICE_FLOOR * 100)
    return -(-listed * pct // 100)


DROP = 100 - int(trade.RELATIVE_PRICE_FLOOR * 100)
FLOOR_200K = ratchet(200_000)


section("the boundary")

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


section("it composes with the absolute per-item floors")

got, why = price(50_000_000, previous=120_000_000, absolute=104_000_000)
check("the HIGHER of the two bounds wins", got == ratchet(120_000_000),
      f"{got:,} -- the ratchet on 120,000,000 is {ratchet(120_000_000):,}, above the 104,000,000 "
      f"floor, so the ratchet binds")
check("...and the reason names the ratchet, not the floor",
      f"{DROP}% below the listed" in why, why)

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

for previous in (104_000_001, 200_000_000, 1_000_000):
    got, _ = price(1_000, previous=previous, absolute=104_000_000)
    check(f"a VIP listed at {previous:,} never goes below its floor",
          got >= 104_000_000, f"{got:,}")


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


section("the ratchet converges on a genuine crash")

_want = math.ceil(math.log(0.5) / math.log(trade.RELATIVE_PRICE_FLOOR))
listed, steps = 200_000, []
for _ in range(_want + 5):
    listed, why = trade.choose_price(100_000, 0, listed, 0)
    steps.append(listed)
    if not why:
        break
check("a real crash is followed, not blocked", steps[-1] == 100_000,
      f"{steps} -- a hard floor would have stopped at 180,000 forever")
check("...taking several cycles, not one", _want <= len(steps) <= _want + 3,
      f"{len(steps)} cycles: {steps}")
check("...and never rising on the way down",
      all(b <= a for a, b in zip(steps, steps[1:])), f"{steps}")


raise SystemExit(summary())
