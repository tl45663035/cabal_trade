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

from PIL import Image

entries = json.loads(INDEX.read_text(encoding="utf-8"))
usable = [e for e in entries
          if e.get("matched") and e.get("rows") and (FRAMES / e["file"]).exists()]
stale = [e for e in entries if not e.get("matched")]


def _captured_name(entry):
    rows = entry.get("rows") or []
    return rows[0][1] if rows and len(rows[0]) > 1 else ""


def _fold(name):
    return trade._floor_key(
        trade.item_name(trade._PACK_ANYWHERE.sub(" ", name or "")))


retired = []
current = []
for e in usable:
    want = trade.FAVOURITE_SLOTS.get(e.get("slot"), "")
    if want and _captured_name(e) and _fold(want) != _fold(_captured_name(e)) \
            and not _fold(_captured_name(e)).startswith(_fold(want)):
        retired.append(e)
    else:
        current.append(e)
usable = current

print(f"{len(entries)} frames indexed: {len(usable)} usable, "
      f"{len(stale)} recorded as stale and excluded")
if retired:
    by_slot = {}
    for e in retired:
        by_slot.setdefault(e["slot"], _captured_name(e))
    print(f"{len(retired)} frame(s) captured under a PREVIOUS favourites "
          f"layout and quarantined:")
    for slot in sorted(by_slot):
        print(f"    slot {slot}: captured as {by_slot[slot]!r}, now "
              f"{trade.FAVOURITE_SLOTS.get(slot, '?')!r} -- needs re-capturing")
    print("    NOTE: these slots have NO golden coverage until re-captured.")

READ = {}
for e in usable:
    READ[e["file"]] = trade.read_purchase_rows(Image.open(FRAMES / e["file"]))


section("A. every frame reads as the slot that was clicked")

for e in usable:
    rows = READ[e["file"]]
    slot = e["slot"]
    expect(f"{e['file']}: rows were read", len(rows) >= 1, f"{len(rows)}")
    expect(f"{e['file']}: matches slot {slot}",
           trade.offers_match_slot(slot, rows),
           f"first row {rows[0].name!r}" if rows else "no rows")
    partner = slot + 1 if slot % 2 else slot - 1
    expect(f"{e['file']}: NOT mistaken for slot {partner}",
           not trade.offers_match_slot(partner, rows),
           f"slot {partner} accepted {rows[0].name!r}" if rows else "")

for e in stale:
    expect(f"{e['file']}: correctly recorded as stale",
           not e.get("matched"), "")


section("B. the sort order the game guarantees, which the reader does not")

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
expect("divisibility is common, as a sanity signal",
       total_multi == 0 or total_exact >= total_multi * 0.5,
       f"{total_exact}/{total_multi} -- a sharp drop here would suggest the "
       f"pack or the price column had started misreading")


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
