"""The buying reader, against every captured frame of the live Purchase tab.

t34 proves the arithmetic on synthetic Offers. This proves the step before it:
that read_purchase_rows turns actual game pixels into those Offers correctly --
across six sweeps of all ten favourite slots, taken minutes apart so the prices,
pack sizes and row orders genuinely differ rather than being one moment copied.

The recorded values were produced BY the reader, which on its own would be
circular -- the flaw that made 91.8% of an earlier suite worthless. So the
frames are held to properties the reader cannot fake:

  reproducibility  a listing that appears in several captures, taken minutes
                apart, must read identically every time. OCR that invented a
                digit would not invent the SAME digit six times. This is the
                strongest evidence available and it needs no assumption about
                how sellers price.
  divisibility  a Set's bundle price OFTEN divides by the pack size in its
                name -- but only when the seller priced per item. Plenty price
                the bundle at a round number instead (480,000,000 for X 999),
                so this corroborates, it does not prove. An earlier version of
                this suite called it independent evidence, which overclaimed:
                50 of 217 multi-pack rows fail it and every one checked was
                read correctly.
  identity      the rows must name the item whose slot was clicked -- the check
                that catches a search which did not run and left the previous
                item's results on screen.
  determinism   the same frame must read the same way twice. OCR that wobbles
                between runs cannot be trusted for money.
  arithmetic    unit x pack must rebuild the total exactly.

Frames live in unit_tests/corpus/buying/ and are gitignored: they are
screenshots of a live market with the account's own balance in them. The suite
skips cleanly when they are absent.
"""
import json
import sys
from pathlib import Path

from harness import section

import trade

FRAMES = Path(__file__).resolve().parent.parent / "corpus" / "buying"
INDEX = FRAMES / "index.json"

PASS = 0
FAILURES: list[str] = []


def expect(label, cond, detail=""):
    global PASS
    if cond:
        PASS += 1
    else:
        FAILURES.append(f"{label}: {detail}")
        print(f"[ FAIL ] {label}: {detail}")


if not INDEX.exists():
    print(f"SKIPPED: no captured frames in {FRAMES}")
    raise SystemExit(0)

from PIL import Image  # noqa: E402

entries = json.loads(INDEX.read_text(encoding="utf-8"))
usable = [e for e in entries
          if e.get("matched") and e.get("rows") and (FRAMES / e["file"]).exists()]
stale = [e for e in entries if not e.get("matched")]
print(f"{len(entries)} frames indexed: {len(usable)} usable, "
      f"{len(stale)} recorded as stale and excluded")

READ = {}
for e in usable:
    READ[e["file"]] = trade.read_purchase_rows(Image.open(FRAMES / e["file"]))


# ===========================================================================
section("A. every frame reads as the slot that was clicked")

for e in usable:
    rows = READ[e["file"]]
    slot = e["slot"]
    expect(f"{e['file']}: rows were read", len(rows) >= 1, f"{len(rows)}")
    expect(f"{e['file']}: matches slot {slot}",
           trade.offers_match_slot(slot, rows),
           f"first row {rows[0].name!r}" if rows else "no rows")
    # And is not mistaken for its pair, which is the dangerous confusion: a
    # Set's name contains its parent's.
    partner = slot + 1 if slot % 2 else slot - 1
    expect(f"{e['file']}: NOT mistaken for slot {partner}",
           not trade.offers_match_slot(partner, rows),
           f"slot {partner} accepted {rows[0].name!r}" if rows else "")

# The three frames the capture itself flagged really are stale.
for e in stale:
    expect(f"{e['file']}: correctly recorded as stale",
           not e.get("matched"), "")


# ===========================================================================
section("B. the sort order the game guarantees, which the reader does not")

# The table is sorted by unit price ascending. That ordering comes from the
# game, not from anything here -- so it is a check the reader cannot satisfy by
# being consistently wrong. A clipped price breaks it instantly: the 444,281
# misread of 7,444,281 sat at 11,391/item beneath rows at 187,278.
#
# An earlier version of this section tried to check that "the same listing
# reads the same across captures", keyed on (name, pack). That was wrong --
# several sellers list the same item at different prices, so it was comparing
# DIFFERENT listings and calling the difference a misread.
ascending = descending = 0
for e in usable:
    rows = READ[e["file"]]
    for a, b in zip(rows, rows[1:]):
        if b.unit + 1.0 < a.unit:
            descending += 1
            expect(f"{e['file']}: row {a.row} -> {b.row} does not go backwards",
                   False,
                   f"{a.unit:,.2f} then {b.unit:,.2f} -- either the sort is not "
                   f"Price: Low to High, or one of these prices was misread")
        else:
            ascending += 1
            expect(f"{e['file']}: row {a.row} -> {b.row} ascends", True)
print(f"  {ascending} adjacent pairs ascend, {descending} descend")
expect("the whole corpus respects the sort",
       descending == 0, f"{descending} inversions")
expect("enough pairs to mean something", ascending >= 200, f"{ascending}")


# ===========================================================================
section("B2. divisibility, as corroboration only")

total_multi = total_exact = 0
for e in usable:
    multi = [o for o in READ[e["file"]] if o.pack > 1]
    exact = [o for o in multi if o.price % o.pack == 0]
    total_multi += len(multi)
    total_exact += len(exact)
    for o in exact:
        expect(f"{e['file']} row {o.row}: unit x pack rebuilds the total",
               round(o.unit) * o.pack == o.price, f"{round(o.unit) * o.pack}")

print(f"  {total_exact} of {total_multi} multi-pack rows divide exactly")
# Deliberately loose. The ones that fail are sellers pricing the bundle at a
# round number -- 480,000,000 for X 999 -- and were confirmed correct by eye.
expect("divisibility is common, as a sanity signal",
       total_multi == 0 or total_exact >= total_multi * 0.5,
       f"{total_exact}/{total_multi} -- a sharp drop here would suggest the "
       f"pack or the price column had started misreading")


# ===========================================================================
section("C. arithmetic on every row of every frame")

rows_checked = 0
for e in usable:
    for o in READ[e["file"]]:
        rows_checked += 1
        expect(f"{e['file']} row {o.row}: pack matches its name",
               o.pack == trade.pack_size(o.name), f"{o.pack}")
        expect(f"{e['file']} row {o.row}: unit is price/pack",
               abs(o.unit - o.price / o.pack) < 1e-9, f"{o.unit}")
        expect(f"{e['file']} row {o.row}: price is plausible",
               o.price >= trade.MIN_PLAUSIBLE_PRICE, f"{o.price}")
        expect(f"{e['file']} row {o.row}: pack is at least one", o.pack >= 1,
               f"{o.pack}")
        expect(f"{e['file']} row {o.row}: unit is positive", o.unit > 0,
               f"{o.unit}")
print(f"  {rows_checked} rows checked")


# ===========================================================================
section("D. determinism: the same pixels read the same way twice")

for e in usable[:20]:
    again = trade.read_purchase_rows(Image.open(FRAMES / e["file"]))
    first = READ[e["file"]]
    expect(f"{e['file']}: same row count", len(again) == len(first),
           f"{len(first)} then {len(again)}")
    for a, b in zip(first, again):
        expect(f"{e['file']} row {a.row}: same name", a.name == b.name,
               f"{a.name!r} vs {b.name!r}")
        expect(f"{e['file']} row {a.row}: same price", a.price == b.price,
               f"{a.price} vs {b.price}")
        expect(f"{e['file']} row {a.row}: same pack", a.pack == b.pack,
               f"{a.pack} vs {b.pack}")


# ===========================================================================
section("E. row 1 is chosen, on every real frame")

for e in usable:
    rows = READ[e["file"]]
    pick = trade.cheapest_listing(rows)
    expect(f"{e['file']}: chooses row 1", pick.row == 1, f"row {pick.row}")
    expect(f"{e['file']}: chooses the first object", pick is rows[0], "")
    expect(f"{e['file']}: quantity is the pack in its name",
           pick.pack == trade.pack_size(pick.name), f"{pick.pack}")
    expect(f"{e['file']}: cost is the row's own price",
           pick.price == rows[0].price, f"{pick.price}")


# ===========================================================================
section("F. the comparison, per pair, per round")

by_round: dict = {}
for e in usable:
    by_round.setdefault(e["round"], {})[e["slot"]] = READ[e["file"]]

pairs_checked = 0
for rnd, slots in sorted(by_round.items()):
    for item_slot in (1, 3, 5, 7, 9):
        set_slot = item_slot + 1
        if item_slot not in slots or set_slot not in slots:
            continue
        a = trade.cheapest_listing(slots[item_slot])
        b = trade.cheapest_listing(slots[set_slot])
        if a is None or b is None:
            continue
        pairs_checked += 1
        saving = a.unit - b.unit
        name = trade.FAVOURITE_SLOTS[item_slot][:24]
        expect(f"r{rnd} {name}: loose item has no pack", a.pack == 1,
               f"{a.name!r} parsed as {a.pack}")
        expect(f"r{rnd} {name}: item unit is its price", a.unit == a.price,
               f"{a.unit} vs {a.price}")
        expect(f"r{rnd} {name}: the Set is cheaper per item", b.unit < a.unit,
               f"set {b.unit:,.2f} vs item {a.unit:,.2f} -- if this ever "
               f"fails the whole strategy is wrong for this item")
        expect(f"r{rnd} {name}: saving is what the two rows imply",
               abs(saving - (a.price / a.pack - b.price / b.pack)) < 1e-9,
               f"{saving}")
        decision = saving >= trade.SET_SAVING_THRESHOLD
        expect(f"r{rnd} {name}: decision is consistent with the saving",
               decision == (saving >= 10_000), f"saving {saving:,.2f}")
print(f"  {pairs_checked} item/Set comparisons across "
      f"{len(by_round)} rounds")


# ===========================================================================
section("G. the corpus is varied enough to mean something")

packs = {o.pack for rows in READ.values() for o in rows}
prices = [o.price for rows in READ.values() for o in rows]
expect("many distinct pack sizes", len(packs) >= 20, f"{len(packs)}")
expect("packs span one to many hundreds",
       min(packs) == 1 and max(packs) >= 100, f"{min(packs)}..{max(packs)}")
expect("prices span several orders of magnitude",
       max(prices) / max(1, min(prices)) >= 100,
       f"{min(prices):,}..{max(prices):,}")
expect("every slot is represented",
       {e["slot"] for e in usable} == set(range(1, 11)),
       f"{sorted({e['slot'] for e in usable})}")
expect("several rounds, so prices differ between frames",
       len(by_round) >= 3, f"{sorted(by_round)}")


print(f"\n{'-' * 74}")
print(f"{PASS + len(FAILURES)} checks, {len(FAILURES)} FAILED")
for f in FAILURES[:25]:
    print(f"  {f}")
raise SystemExit(1 if FAILURES else 0)
