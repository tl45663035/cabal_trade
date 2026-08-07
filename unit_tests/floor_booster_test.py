"""Two floors on names that differ by four characters, one a prefix of the other.

"Epic Booster (High)" and "Epic Booster (Highest)" score 0.909 against each
other, so every read of either matches BOTH catalogue entries. That breaks the
rule the floor lookup had used since it was written -- take the highest floor of
everything that matches -- because under it the 24,000,000 item inherited the
44,000,000 floor and would never have sold.

Two directions matter and they pull against each other:

  * a clean read of the cheaper item must get its OWN, cheaper floor, or the
    floor is worse than useless: the item is parked at a price nobody pays;
  * ANY damaged or clipped read must never come back below 44,000,000, because
    that sells a 44,000,000 item for 24,000,000. This is the direction the user
    has said twice must never happen.

So the lookup decides by best match, and treats an inexact read of a
prefix-related pair as undecidable -- falling back to the higher floor. This
suite measures both directions over realistic damage rather than clean strings.

Verified against the live shop on 2026-08-06, where both were listed: the
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
HIGH_FLOOR = next(f for _, c, f in m.ITEM_PRICE_FLOORS if c == HIGH)

fails = []


def check(cond, label):
    if not cond:
        fails.append(label)
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}")


print(f"floors under test: (Highest) {HIGHEST_FLOOR:,}  (High) {HIGH_FLOOR:,}")
check(HIGHEST_FLOOR > HIGH_FLOOR,
      "the (Highest) floor is the dearer of the two")

print("\n-- each clean name binds to its OWN floor")
check(m.item_price_floor(HIGHEST) == HIGHEST_FLOOR,
      f"{HIGHEST!r} -> {HIGHEST_FLOOR:,}")
check(m.item_price_floor(HIGH) == HIGH_FLOOR,
      f"{HIGH!r} -> {HIGH_FLOOR:,}")

print("\n-- the cheaper item keeps its cheaper floor through ordinary reads")
for name in (HIGH,
             HIGH + " Use Period: 30 days",
             HIGH + " Grade 1",
             "Ep1c Booster (Hlgh)",
             "EpicBooster(High)",
             "Epic  Booster  (High)"):
    check(m.item_price_floor(name) == HIGH_FLOOR,
          f"{name[:38]!r} keeps {HIGH_FLOOR:,}")

print("\n-- glyph damage on the dearer item never drops below its floor")
for name in ("Ep1c Booster (Highest)",
             "Epic Booster (H1ghest)",
             "Epic Booster (Hlghest)",
             "Epic Booster (Highest]",
             "Epic 8ooster (Highest)",
             "EpicBooster(Highest)",
             "epic booster (highest)",
             "Epic Booster (Highest) Use Period: 30 days"):
    got = m.item_price_floor(name)
    check(got >= HIGHEST_FLOOR, f"{name[:38]!r} -> {got:,} >= {HIGHEST_FLOOR:,}")

print("\n-- clipped at EVERY length, the dearer item is never underpriced")
# The one exception is measured, not waved away: clipping to exactly
# "Epic Booster (High" produces the (High) entry's key character for
# character. Identical strings cannot be told apart by any name rule, and
# forcing that string to the dearer floor would mean the cheaper item always
# carried it and never sold.
COLLISION = "Epic Booster (High"
for n in range(4, len(HIGHEST) + 1):
    name = HIGHEST[:n]
    got = m.item_price_floor(name)
    if name == COLLISION:
        check(got == HIGH_FLOOR,
              f"{name!r} is the known collision -> {got:,} (documented)")
        continue
    check(got == 0 or got >= HIGHEST_FLOOR,
          f"{name!r} -> {got:,} (0 or >= {HIGHEST_FLOOR:,})")

print("\n-- the floors do not leak onto anything else on this shop")
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
# matching sells a 44,000,000 item for 24,000,000. If such an item is ever
# listed, give it its own entry -- exactly as the gempack note says.
check(m.item_price_floor("Epic Boost Pack") == HIGHEST_FLOOR,
      f"'Epic Boost Pack' inherits {HIGHEST_FLOOR:,} via the shared token "
      f"(over-match is the safe direction, and is documented)")

print("\n-- the other floors are untouched")
for catalogue, want in ((m.ITEM_PRICE_FLOORS[0][1], m.ITEM_PRICE_FLOORS[0][2]),
                        (m.ITEM_PRICE_FLOORS[1][1], m.ITEM_PRICE_FLOORS[1][2]),
                        (m.ITEM_PRICE_FLOORS[2][1], m.ITEM_PRICE_FLOORS[2][2])):
    got = m.item_price_floor(catalogue)
    check(got == want, f"{catalogue[:36]!r} still {want:,}")

print("\n-- the strictest floor still comes from the dearest entry")
check(m.strictest_price_floor() == max(f for *_, f in m.ITEM_PRICE_FLOORS),
      f"strictest_price_floor() == {m.strictest_price_floor():,}")
check(m.strictest_price_floor() > HIGHEST_FLOOR,
      "an unnameable item is still floored above both boosters")

print(f"\n{len(fails)} failure(s)")
for f in fails:
    print(f"  {f}")
raise SystemExit(1 if fails else 0)
