"""The operator's floors are these NUMBERS. Stated once, in one place.

WHY THIS FILE EXISTS. On 2026-08-09 the VIP row was deleted from
ITEM_PRICE_FLOORS entirely -- `item_price_floor("Yekaterina VIP Membership")`
returning 0 -- and three of the five floor suites stayed GREEN:

    floor_catalogue_test.py      STILL GREEN
    floor_fuzz_test.py           STILL GREEN
    floor_booster_test.py        STILL GREEN
    floor_siena_test.py          crashed (StopIteration, not a clean failure)
    floor_paths_test.py          correctly failed

Every one of them reads the expected floor out of the table it is testing.
floor_fuzz_test.py:16 is the purest case -- `FLOOR = m.item_price_floor(...)`,
commented "derived, never restated" -- so deleting the row makes FLOOR 0 and
every check compares 0 to 0. floor_catalogue_test.py is
`for token, label, floor in ITEM_PRICE_FLOORS: check(item_price_floor(label)
== floor)`, which is the dict asserting it equals itself.

The suites' stated reason for avoiding literals is that a floor is an operator
setting and a test should not fight a deliberate change. That gets it exactly
backwards: a suite that cannot go red when the number CHANGES cannot go red
when the number is WRONG -- and on 2026-08-04 a request for 110,000,000 shipped
as 105,000,000, the commit message claimed 110,000,000, all three floor suites
passed, and four VIPs then sold at 109,999,999.

So: literals, here, once. Changing a floor is a deliberate act and should
require editing the number in two places -- the catalogue and this file --
with the diff showing both. Every other floor suite may keep deriving; this is
the one that pins.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


# item as the operator names it  ->  the floor, in Alz
#
# UPDATE BOTH SIDES TOGETHER. If you are here because this file failed, the
# question to answer is "did I mean to move this floor?" -- not "how do I make
# the test pass".
FLOORS = {
    "Yekaterina VIP Membership": 104_000_000,
    "Siena's Unbinding Stone":    71_000_000,
    "Force Gem Package (x400)":  175_000_000,
    "Epic Booster (Highest)":     44_000_000,
    # PER UNIT. Added 2026-08-15 at the operator's instruction: "the absolute
    # price floor for Chaos set is 690k per ... no matter what, always price
    # floor is 690k regardless of what market says". Chaos lists compressed
    # bundles, so item_price_floor scales this by the count in the name -- the
    # bare name here is one unit. See the bundle checks below.
    "Chaos Core Set":                690_000,
}

for name, want in FLOORS.items():
    got = m.item_price_floor(name)
    check(got == want,
          f"{name} must floor at exactly {want:,} Alz, got {got:,}")
    check(got != 0,
          f"{name} resolved to NO floor at all -- the catalogue entry is "
          f"missing or its name no longer matches")

# A COMPRESSED BUNDLE IS ONE ROW PRICED FOR ALL OF IT, so the absolute floor
# scales by the count in its name. A flat per-unit figure would be hundreds of
# times too low and would never bind on the listing it exists to guard.
for bundle, count in (("Chaos Core Set X 197", 197),
                      ("Chaos Core Set X 250", 250),
                      ("Chaos Core Set", 1)):
    want_bundle = 690_000 * count
    got_bundle = m.item_price_floor(bundle)
    check(got_bundle == want_bundle,
          f"{bundle} must floor at {want_bundle:,} ({count} x 690,000), got "
          f"{got_bundle:,}")

# AND THE RAW MATERIAL MUST NOT INHERIT IT. "Chaos Core" is what chaos BUYS;
# it folds to a strict prefix of "Chaos Core Set" and scores 0.83 against it,
# which clears the similarity bar. Flooring it would be both wrong and
# expensive. Same containment trap as 'siena' vs 'unbinding'.
for raw in ("Chaos Core", "Chaos Core X 250", "chaos core"):
    check(m.item_price_floor(raw) == 0,
          f"{raw!r} must have NO absolute floor -- it is the raw material, "
          f"not the product. Got {m.item_price_floor(raw):,}")

# A MANGLED PACK MARKER MUST NOT PRICE A BUNDLE AT A FRACTION.
#
# item_price_floor scales a per-unit catalogue floor by the count in the name.
# pack_size returns 1 when its end-anchored pattern misses -- right for an item
# with no marker, catastrophic for one whose marker the OCR mangled. Measured
# 2026-08-15: "Chaos Core Set X 25O" (zero read as O) scored 690,000 against a
# true 172,500,000, a 250x collapse, and it takes the whole floor stack with it
# because market_floor keys on the folded name and misses too.
for _bad in ("Chaos Core Set X 25O",                      # 0 -> O
             "Chaos Core Set X 2SO",                      # 5 -> S as well
             "Chaos Core Set X 250 Use Period: 30 days"):  # trailer
    check(m.item_price_floor(_bad) == 0,
          f"{_bad!r} has a marker that did not parse, so it REFUSES to price "
          f"rather than pricing at 1 unit. Got "
          f"{m.item_price_floor(_bad):,}")

# A CALLER THAT KNOWS THE COUNT OVERRIDES THE NAME.
check(m.item_price_floor("Chaos Core Set X 25O", units=250) == 690_000 * 250,
      "a registration that knows it is listing 250 units floors at "
      f"{690_000 * 250:,} whatever the label reads")
check(m.item_price_floor("Chaos Core Set X 250", units=10) == 690_000 * 250,
      "and the LARGER of the two wins -- a floor is a minimum, so only 'too "
      "low' is dangerous")

# A NAME WITHOUT A MARKER IS ONE UNIT, NOT A MANGLED READ.
check(m.item_price_floor("Chaos Core Set") == 690_000,
      "the bare catalogue name still prices at one unit")

# AND A PARENTHESISED COUNT IS PART OF THE NAME. "Force Gem Package (x400)" is
# what the item is CALLED; reading its (x400) as a broken marker zeroed a
# 175,000,000 floor when this guard was first written.
check(m.item_price_floor("Force Gem Package (x400)") == 175_000_000,
      f"the gem pack keeps its floor, got "
      f"{m.item_price_floor('Force Gem Package (x400)'):,}")
for _vip in ("Yekaterina VIP Membership", "Siena's Unbinding Stone",
             "Epic Booster (Highest)"):
    check(m.item_price_floor(_vip) > 0,
          f"{_vip} is untouched by the marker guard")

# The catalogue holds these and only these. An entry appearing without a
# pinned amount is a floor nobody has stated.
catalogue = {label for _token, label, _floor in m.ITEM_PRICE_FLOORS}
check(catalogue == set(FLOORS),
      f"ITEM_PRICE_FLOORS and this file disagree about WHICH items have "
      f"floors.\n  only in trade.py: {sorted(catalogue - set(FLOORS))}\n"
      f"  only here:        {sorted(set(FLOORS) - catalogue)}")

# The raw tuples carry the same numbers. item_price_floor could in principle
# return a right answer from a wrong row.
for _token, label, floor in m.ITEM_PRICE_FLOORS:
    if label in FLOORS:
        check(floor == FLOORS[label],
              f"the ITEM_PRICE_FLOORS row for {label} says {floor:,}, this "
              f"file says {FLOORS[label]:,}")

# strictest_price_floor gates `--price` on an unnamed item, so it must be the
# real maximum rather than whatever happens to be first.
check(m.strictest_price_floor() == max(FLOORS.values()),
      f"the strictest floor is {max(FLOORS.values()):,}, got "
      f"{m.strictest_price_floor():,}")

# And the floors must reach listing_floor, which is what register_item calls --
# in BOTH positions of the cost-floor flag, because that flag is not allowed to
# touch them.
_saved = m.COST_FLOOR_ON_RELIST
try:
    for state in (True, False):
        m.COST_FLOOR_ON_RELIST = state
        for name, want in FLOORS.items():
            got, _why = m.listing_floor(name)
            check(got >= want,
                  f"listing_floor({name}) must be at least {want:,} with "
                  f"COST_FLOOR_ON_RELIST={state}, got {got:,}")
finally:
    m.COST_FLOOR_ON_RELIST = _saved


print(f"floor_amounts_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
