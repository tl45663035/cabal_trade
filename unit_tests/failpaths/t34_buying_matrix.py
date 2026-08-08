"""Exhaustive cover of the buying arithmetic, across all ten favourite slots.

Money leaves the account here, so the four things that must be right get
measured rather than argued:

    quantity        how many items one listing actually contains
    price per item  the bundle price divided by that quantity
    comparison      loose item against its Set, on the same row the buy takes
    how many bought exactly one listing, of the size shown on row 1

Generated rather than hand-written. Every case is derived from the shapes the
live shop produced on 2026-08-07 -- pack suffixes, the game's inconsistent
spacing before the bracket, OCR damage, clipped prices, stale result tables --
and swept across all ten slots and their pairings.

Failures print; passes are counted silently, because ten thousand "ok" lines
hide the one line that matters.
"""
import random
import sys

from harness import section

import trade

PASS = 0
FAILURES: list[str] = []


def expect(label, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"[ FAIL ] {label}: {detail}")


def offer(row, name, price, pack=None):
    return trade.Offer(row=row, name=name, price=price,
                       pack=trade.pack_size(name) if pack is None else pack,
                       y=340 + (row - 1) * 76)


ITEM_SLOTS = [s for s in trade.FAVOURITE_SLOTS if s % 2 == 1]
SET_SLOTS = [s for s in trade.FAVOURITE_SLOTS if s % 2 == 0]
PACKS = [1, 2, 5, 10, 30, 39, 62, 100, 124, 159, 170, 200, 231, 250, 283,
         330, 362, 500, 643, 675, 999, 1000, 1250]
UNITS = [98_000, 100_000, 187_278, 190_879, 199_999, 205_000, 209_800,
         209_999, 381_614, 514_999, 1_000_000]

rng = random.Random(20260807)


# ===========================================================================
section("A. quantity: the pack size parsed out of the name")

for slot, base in trade.FAVOURITE_SLOTS.items():
    for pack in PACKS:
        # As the game writes it.
        expect(f"slot {slot} {base!r} X {pack}",
               trade.pack_size(f"{base} X {pack}") == pack,
               f"got {trade.pack_size(f'{base} X {pack}')}")
        # With a thousands separator, which the game uses past 999.
        expect(f"slot {slot} {base!r} X {pack:,}",
               trade.pack_size(f"{base} X {pack:,}") == pack,
               f"got {trade.pack_size(f'{base} X {pack:,}')}")
        # Spacing the OCR sometimes loses or doubles.
        for variant in (f"{base}X{pack}", f"{base}  X  {pack}", f"{base} X{pack} "):
            expect(f"spacing {variant[-14:]!r}",
                   trade.pack_size(variant) == pack,
                   f"got {trade.pack_size(variant)}")

# A name with no pack suffix is one item, never zero.
for slot, base in trade.FAVOURITE_SLOTS.items():
    expect(f"slot {slot}: bare {base!r} is one item",
           trade.pack_size(base) == 1, f"got {trade.pack_size(base)}")
    expect(f"slot {slot}: trailing space", trade.pack_size(base + "  ") == 1, "")

# Things that look like a pack suffix but are not.
for junk, want in (("Force Core Set (High) X", 1),
                   ("Force Core Set (High) X abc", 1),
                   ("Force Core Set (High) 62", 1),
                   ("X 62 Force Core Set (High)", 1),
                   ("Force Core Set (High) X 0", 1),
                   ("Force Core Set (High) X -5", 1)):   # a minus is not a pack
    expect(f"junk suffix {junk!r}", trade.pack_size(junk) == want,
           f"got {trade.pack_size(junk)}, wanted {want}")


# ===========================================================================
section("B. price per item: the bundle divided by the quantity")

for pack in PACKS:
    for unit in UNITS:
        total = unit * pack
        o = offer(1, f"Force Core Set (High) X {pack}", total)
        expect(f"{pack} x {unit:,} -> {unit:,}/item",
               abs(o.unit - unit) < 1e-6, f"got {o.unit}")
        expect(f"{pack} x {unit:,}: pack read back", o.pack == pack,
               f"got {o.pack}")
        # The whole point: the bundle must be worth pack x unit.
        expect(f"{pack} x {unit:,}: total consistent",
               abs(o.unit * o.pack - total) < 1e-6, f"{o.unit * o.pack}")

# A price that does not divide cleanly still yields a sane per-item figure.
for pack in (7, 39, 124, 283, 643):
    for total in (1_000_001, 23_222_500, 53_000_000, 61_802_070):
        o = offer(1, f"Set X {pack}", total)
        expect(f"{total:,}/{pack} is exact division",
               abs(o.unit - total / pack) < 1e-9, f"{o.unit}")
        expect(f"{total:,}/{pack} is positive", o.unit > 0, f"{o.unit}")

# A loose item is its own price.
for unit in UNITS:
    o = offer(1, "Force Core(High)", unit)
    expect(f"loose item at {unit:,} is {unit:,}/item", o.unit == unit, f"{o.unit}")
    expect(f"loose item at {unit:,} has pack 1", o.pack == 1, f"{o.pack}")


# ===========================================================================
section("C. selection: row 1, whatever the rest of the table says")

for trial in range(200):
    n = rng.randint(1, trade.PURCHASE_ROWS)
    rows = []
    for i in range(1, n + 1):
        pack = rng.choice(PACKS)
        unit = rng.choice(UNITS)
        rows.append(offer(i, f"Force Core Set (High) X {pack}", unit * pack))
    pick = trade.cheapest_listing(rows)
    expect(f"trial {trial}: picks row 1", pick.row == 1,
           f"picked row {pick.row} of {n}")
    expect(f"trial {trial}: picks the row 1 object", pick is rows[0], "")

expect("an empty table yields nothing", trade.cheapest_listing([]) is None, "")

# Explicitly: rows that look better later must NOT win.
tempting = [offer(1, "Set X 10", 2_000_000),          # 200,000 each
            offer(2, "Set X 100", 1_000_000),         # 10,000 each, huge bargain
            offer(3, "Set X 50", 500_000)]            # 10,000 each, cheapest bill
expect("a later bargain does not displace row 1",
       trade.cheapest_listing(tempting).row == 1, "")
expect("a later cheapest-bill does not displace row 1",
       trade.cheapest_listing(tempting).price == 2_000_000, "")


# ===========================================================================
section("D. the search actually ran: every slot, both directions")

def rows_for(slot, count=5):
    base = trade.FAVOURITE_SLOTS[slot]
    out = []
    for i in range(1, count + 1):
        pack = rng.choice(PACKS)
        name = f"{base} X {pack}" if "Set" in base else base
        out.append(offer(i, name, rng.choice(UNITS) * (pack if "Set" in base else 1)))
    return out

for slot in trade.FAVOURITE_SLOTS:
    mine = rows_for(slot)
    expect(f"slot {slot} recognises its own results",
           trade.offers_match_slot(slot, mine), f"{mine[0].name!r}")
    for other in trade.FAVOURITE_SLOTS:
        if other == slot:
            continue
        expect(f"slot {other} rejects slot {slot}'s results",
               not trade.offers_match_slot(other, mine),
               f"slot {other} accepted {mine[0].name!r} -- a stale table read "
               f"as a fresh search compares an item against itself")

# The pairing is the dangerous direction: a Set's name CONTAINS its parent's.
for item_slot in ITEM_SLOTS:
    set_slot = item_slot + 1
    set_rows = rows_for(set_slot)
    item_rows = rows_for(item_slot)
    expect(f"slot {item_slot} rejects its own Set's rows",
           not trade.offers_match_slot(item_slot, set_rows),
           "this is the 'saving 0.00/each' failure")
    expect(f"slot {set_slot} rejects the loose item's rows",
           not trade.offers_match_slot(set_slot, item_rows), "")

expect("nothing on screen matches nothing",
       not trade.offers_match_slot(7, []), "")
for slot in trade.FAVOURITE_SLOTS:
    expect(f"slot {slot} rejects an unrelated item",
           not trade.offers_match_slot(
               slot, [offer(1, "Siena's Unbinding Stone", 75_000_000)]), "")


# ===========================================================================
section("E. the comparison and the buy/no-buy decision")

fake: dict = {}


def fake_search(slot, **kw):
    return fake.get(slot, [])


bought: list = []


def fake_buy(target, **kw):
    bought.append(target)
    return True, ""


real_search, real_buy = trade.run_favourite_search, trade.buy_offer
# affordable() reads the Alz balance off the SCREEN. Left real, this section
# OCR'd the desktop to decide whether each case passed -- and a low read called
# halt_buying(), which latches for the rest of the process and failed every
# case after it. This matrix is about the saving comparison; whether the
# account can cover the price is t33's subject.
real_afford = trade.affordable
trade.run_favourite_search, trade.buy_offer = fake_search, fake_buy
trade.affordable = lambda price, source=None: True
try:
    for item_slot in ITEM_SLOTS:
        set_slot = item_slot + 1
        item_name = trade.FAVOURITE_SLOTS[item_slot]
        set_name = trade.FAVOURITE_SLOTS[set_slot]
        # PER ITEM, not one global figure. Upgrade Core(Highest) and Force
        # Core(High) were dropped to 5,000 on 2026-08-08 because they turn over
        # faster than they earn -- stock that does not move earns nothing at
        # all. A single SET_SAVING_THRESHOLD here asserted the old rule and
        # reported the new one as eight failures.
        threshold = trade.price_diff_floor_for(item_name)
        for item_unit in UNITS:
            for saving in (-50_000, -1, 0, 1, threshold - 1, threshold,
                           threshold + 1, 22_522, 100_000):
                set_unit = item_unit - saving
                if set_unit <= 0:
                    continue
                pack = rng.choice(PACKS)
                fake.clear()
                bought.clear()
                fake[item_slot] = [offer(1, item_name, item_unit)]
                fake[set_slot] = [offer(1, f"{set_name} X {pack}", set_unit * pack)]
                # BUY_HALTED is process-lifetime by design -- a wrong item map
                # must not be retried. That makes it poison inside a matrix:
                # one halted case silently turns every later one into "buying
                # is off". Cleared per case so each is independent.
                trade.BUY_HALTED, trade.BUY_HALT_REASON = False, ""
                got = trade.buy_cheapest_set(item_slot, verbose=False)
                want = saving >= threshold
                expect(f"slot {item_slot} saving {saving:,} -> "
                       f"{'buy' if want else 'no buy'}",
                       got == want,
                       f"returned {got} for item {item_unit:,} vs set "
                       f"{set_unit:,} (saving {saving:,})")
                expect(f"slot {item_slot} saving {saving:,}: bought "
                       f"{'one' if want else 'nothing'}",
                       len(bought) == (1 if want else 0),
                       f"bought {len(bought)}")
                if want and bought:
                    expect(f"slot {item_slot} saving {saving:,}: bought row 1",
                           bought[0].row == 1, f"row {bought[0].row}")
                    expect(f"slot {item_slot} saving {saving:,}: correct qty",
                           bought[0].pack == pack,
                           f"pack {bought[0].pack}, wanted {pack}")
                    expect(f"slot {item_slot} saving {saving:,}: correct total",
                           bought[0].price == set_unit * pack,
                           f"{bought[0].price:,}")

    # A Set slot has no Set of its own, so it can never start a comparison.
    for set_slot in SET_SLOTS:
        fake.clear(); bought.clear()
        expect(f"slot {set_slot} (a Set) refuses to be the item side",
               trade.buy_cheapest_set(set_slot, verbose=False) is False, "")
        expect(f"slot {set_slot}: nothing bought", not bought, f"{bought}")

    # A search that did not run yields nothing, and nothing must be bought.
    for item_slot in ITEM_SLOTS:
        fake.clear(); bought.clear()
        fake[item_slot] = []                     # the item search failed
        fake[item_slot + 1] = [offer(1, f"{trade.FAVOURITE_SLOTS[item_slot+1]} X 10",
                                     100_000)]
        expect(f"slot {item_slot}: no item rows -> no buy",
               trade.buy_cheapest_set(item_slot, verbose=False) is False, "")
        expect(f"slot {item_slot}: nothing bought without a comparison",
               not bought, f"{bought}")

        fake.clear(); bought.clear()
        fake[item_slot] = [offer(1, trade.FAVOURITE_SLOTS[item_slot], 209_800)]
        fake[item_slot + 1] = []                 # the Set search failed
        expect(f"slot {item_slot}: no set rows -> no buy",
               trade.buy_cheapest_set(item_slot, verbose=False) is False, "")
        expect(f"slot {item_slot}: nothing bought without Set rows",
               not bought, f"{bought}")

    # Custom thresholds behave monotonically.
    for item_slot in ITEM_SLOTS:
        for thr in (0, 1_000, 10_000, 25_000, 1_000_000):
            for saving in (0, 5_000, 10_000, 22_522, 50_000):
                fake.clear(); bought.clear()
                fake[item_slot] = [offer(1, trade.FAVOURITE_SLOTS[item_slot],
                                         200_000)]
                fake[item_slot + 1] = [
                    offer(1, f"{trade.FAVOURITE_SLOTS[item_slot+1]} X 100",
                          (200_000 - saving) * 100)]
                got = trade.buy_cheapest_set(item_slot, threshold=thr,
                                             verbose=False)
                expect(f"slot {item_slot} thr {thr:,} saving {saving:,}",
                       got == (saving >= thr), f"returned {got}")
finally:
    trade.run_favourite_search, trade.buy_offer = real_search, real_buy
    trade.affordable = real_afford


# ===========================================================================
section("F. how many: exactly one listing, of the size shown")

# Same reasoning as section E: affordable() reads the Alz balance off the
# SCREEN, and a 250-pack at 200,000 each is 50,000,000 -- more than a desktop
# OCR read usually produces, so it called halt_buying(), which latches for the
# rest of the process and failed every case after it. Whether the account can
# cover a price is t33's subject; this section is about how MANY listings a
# purchase takes, and at what size.
trade.run_favourite_search, trade.buy_offer = fake_search, fake_buy
trade.affordable = lambda price, source=None: True
try:
    for item_slot in ITEM_SLOTS:
        for pack in PACKS:
            fake.clear(); bought.clear()
            trade.BUY_HALTED, trade.BUY_HALT_REASON = False, ""
            fake[item_slot] = [offer(1, trade.FAVOURITE_SLOTS[item_slot], 300_000)]
            fake[item_slot + 1] = [
                offer(1, f"{trade.FAVOURITE_SLOTS[item_slot+1]} X {pack}",
                      200_000 * pack),
                offer(2, f"{trade.FAVOURITE_SLOTS[item_slot+1]} X 999",
                      100_000 * 999)]      # a better row that must be ignored
            trade.buy_cheapest_set(item_slot, verbose=False)
            expect(f"slot {item_slot} pack {pack}: exactly one purchase",
                   len(bought) == 1, f"{len(bought)}")
            if bought:
                expect(f"slot {item_slot} pack {pack}: quantity is row 1's",
                       bought[0].pack == pack, f"{bought[0].pack}")
                expect(f"slot {item_slot} pack {pack}: never the tempting row 2",
                       bought[0].row == 1, f"row {bought[0].row}")
                expect(f"slot {item_slot} pack {pack}: paid row 1's price",
                       bought[0].price == 200_000 * pack, f"{bought[0].price:,}")
finally:
    trade.run_favourite_search, trade.buy_offer = real_search, real_buy
    trade.affordable = real_afford


print(f"\n{'-' * 74}")
print(f"{PASS + len(FAILURES)} checks, {len(FAILURES)} FAILED")
for f in FAILURES[:20]:
    print(f"  {f}")
raise SystemExit(1 if FAILURES else 0)
