import sys
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m

HIGHEST = "Epic Booster (Highest)"
HIGH = "Epic Booster (High)"
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

check(m.item_price_floor("Epic Booster (High") >= HIGHEST_FLOOR,
      f"'Epic Booster (High' -- the old 24,000,000 collision -- now returns "
      f"{m.item_price_floor('Epic Booster (High'):,}")

print("\n-- a clean (High) read takes the dearer floor now, and that is known")
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
