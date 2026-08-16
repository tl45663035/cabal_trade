"""The Epic Booster floor: one entry now, and no read may fall below it.

This suite used to be about a PAIR. "Epic Booster (High)" and "Epic Booster
(Highest)" score 0.909 against each other, so every read of either matched both
catalogue entries, and the lookup had to decide by best match rather than by
taking the highest floor -- otherwise the 24,000,000 item inherited the
44,000,000 floor and would never have sold.

"Epic Booster (High)" was removed from the catalogue on 2026-08-07 at the
operator's request; only the Highest grade is listed now. That changes what
there is to test, and in one respect it makes the guarantee STRONGER:

  * before, one clip length was a known, documented exception. Cutting
    "(Highest)" at exactly the character before "est)" gives "Epic Booster
    (High", which was the (High) entry's key character for character --
    identical strings, separable by no name-based rule -- so it took
    24,000,000 for a 44,000,000 item. That exception is now gone;
  * so the claim this file makes is unconditional: NO read of an Epic Booster,
    however damaged or clipped, comes back below 44,000,000. That is the
    direction the operator has said twice must never be crossed.

The cost of the removal is recorded in trade.py beside the entry: a genuine
(High) listing, worth about 25,000,000 live, would now carry a floor above its
own value and never sell. The check at the bottom fires if the entry is ever
put back, because the pair tests have to come back with it.

Verified against the live shop on 2026-08-06, when both were listed: the
catalogue spelling here is the game's own, read off the table.
"""
import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

HIGHEST = "Epic Booster (Highest)"
HIGH = "Epic Booster (High)"
# Read out of the table, so changing a floor cannot leave this green while
# asserting the old number.
HIGHEST_FLOOR = next(f for _, c, f in m.ITEM_PRICE_FLOORS if c == HIGHEST)
HIGH_ENTRY = [f for _, c, f in m.ITEM_PRICE_FLOORS if c == HIGH]

fails = []


def check(cond, label):
    if not cond:
        fails.append(label)
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}")


print(f"floor under test: (Highest) {HIGHEST_FLOOR:,}")
print(f"(High) in the catalogue: {'yes' if HIGH_ENTRY else 'no -- removed'}")

print("\n-- the catalogued name binds to its floor")
check(m.item_price_floor(HIGHEST) == HIGHEST_FLOOR,
      f"{HIGHEST!r} -> {HIGHEST_FLOOR:,}")

print("\n-- glyph damage never drops below the floor")
for name in ("Ep1c Booster (Highest)",
             "Epic Booster (H1ghest)",
             "Epic Booster (Hlghest)",
             "Epic Booster (Highest]",
             "Epic 8ooster (Highest)",
             "EpicBooster(Highest)",
             "epic booster (highest)",
             "Epic Booster (Highest) Use Period: 30 days",
             "Epic  Booster  (Highest)"):
    got = m.item_price_floor(name)
    check(got >= HIGHEST_FLOOR, f"{name[:38]!r} -> {got:,} >= {HIGHEST_FLOOR:,}")

print("\n-- clipped at EVERY length, never underpriced, with no exception")
# The `continue` for a known collision that used to live here is deliberately
# gone. With one entry there is nothing left to be confused with, so the rule
# is now: every clip is either unrecognised (0) or floored at the full amount.
# If this loop ever needs an exception again, the pair has come back -- see the
# check at the bottom.
worst = []
for n in range(4, len(HIGHEST) + 1):
    name = HIGHEST[:n]
    got = m.item_price_floor(name)
    ok = got == 0 or got >= HIGHEST_FLOOR
    if not ok:
        worst.append((name, got))
    check(ok, f"{name!r} -> {got:,} (0 or >= {HIGHEST_FLOOR:,})")
check(not worst,
      f"no clip length is underpriced at all -- the documented exception is "
      f"gone. Offenders: {worst}")

# The clip that WAS the exception, called out by name so the change is visible
# rather than buried in the loop above.
check(m.item_price_floor("Epic Booster (High") >= HIGHEST_FLOOR,
      f"'Epic Booster (High' -- the old 24,000,000 collision -- now returns "
      f"{m.item_price_floor('Epic Booster (High'):,}")

print("\n-- a clean (High) read takes the dearer floor now, and that is known")
# Not an accident and not free. With no (High) entry this scores 0.909 against
# (Highest) and inherits its floor, so a genuine (High) listing would go up at
# 44,000,000 against a live value near 25,000,000 and never sell. Over-match is
# the safe direction -- under-matching sells the dearer grade 20,000,000 short
# -- but it is a real consequence and is asserted so it stays visible.
check(m.item_price_floor(HIGH) == HIGHEST_FLOOR,
      f"{HIGH!r} -> {m.item_price_floor(HIGH):,}: safe, but it would not sell. "
      f"Give it its own entry again before listing one.")

print("\n-- the floor does not leak onto anything else on this shop")
for name in ("Force Core(High)", "Force Core(Highest)", "Force Core (Ultimate)",
             "Force Core(Medium)", "Upgrade Core (Ultimate)",
             "Upgrade Core(Highest)", "Chaos Safeguard X 1",
             "Palladium Coat(FB)", "Palladium Visor(FS)", "SIGmetal Suit (DM)",
             "Archridium Plate(GL)", "Shape Cartridge (Lv. 4) X 27",
             "Booster", "Epic", "Highest"):
    got = m.item_price_floor(name)
    check(got == 0, f"{name!r} gets no Epic Booster floor (got {got:,})")

print("\n-- a hypothetical item carrying the token DOES inherit the floor")
# Deliberate, and the same trade the catalogue already records for 'gempack':
# a different pack size inherits that floor and no token can prevent it. Over-
# matching parks an item at a price nobody pays, which costs a cycle; under-
# matching sells a 44,000,000 item short. If such an item is ever listed, give
# it its own entry -- exactly as the gempack note says.
check(m.item_price_floor("Epic Boost Pack") == HIGHEST_FLOOR,
      f"'Epic Boost Pack' inherits {HIGHEST_FLOOR:,} via the shared token "
      f"(over-match is the safe direction, and is documented)")

print("\n-- the other floors are untouched")
for _token, catalogue, want in m.ITEM_PRICE_FLOORS:
    got = m.item_price_floor(catalogue)
    check(got >= want, f"{catalogue[:36]!r} still at least {want:,} (got {got:,})")

print("\n-- the strictest floor still comes from the dearest entry")
check(m.strictest_price_floor() == max(f for *_, f in m.ITEM_PRICE_FLOORS),
      f"strictest_price_floor() == {m.strictest_price_floor():,}")
check(m.strictest_price_floor() > HIGHEST_FLOOR,
      "an unnameable item is still floored above the booster")

print("\n-- if the (High) entry comes back, so must the pair tests")
check(not HIGH_ENTRY,
      "'Epic Booster (High)' is absent from ITEM_PRICE_FLOORS, which is what "
      "this suite assumes. A failure here means it was put back -- restore the "
      "best-match and collision tests from git history before trusting this "
      "file, because they are what proved the pair could be told apart.")

print(f"\n{len(fails)} failure(s)")
for f in fails:
    print(f"  {f}")
raise SystemExit(1 if fails else 0)
