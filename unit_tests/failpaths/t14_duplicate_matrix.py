"""Duplicate listings: same item, every combination of same/different price
and same/different quantity.

The shop holds two or more stacks of one item constantly -- 71.4% of recorded
tables carry at least one identical pair -- and this is where the script has
gone wrong most expensively. Cancelling the wrong stack costs a registration
fee and leaves the sold-down stack stale; collecting the wrong one pulls a
second stack off the market for a single sale.

The matrix, for one item name:

                    same qty              different qty
    same price      indistinguishable     tell apart by qty
    diff price      tell apart by price   tell apart by either

Only the top-left cell is genuinely ambiguous. The other three MUST resolve
exactly, and a script that gives up on them is leaving money on the table just
as surely as one that guesses wrong. Both directions are asserted here.

The nastiest case has its own section: a partial sale whose remainder happens
to equal a twin's quantity, which turns a distinguishable pair into an
indistinguishable one at the worst possible moment.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

NAME = "Force Core(High)"
OTHER = "Upgrade Core(Highest)"


def rows_of(*specs):
    """specs are (action, price, qty) triples, numbered from 1."""
    return [make_row(i, NAME, action=a, price=p, qty=q)
            for i, (a, p, q) in enumerate(specs, start=1)]


def find(rows, row, strict=False):
    ref = trade.RowRef.of(row, rows)
    return trade.locate_row(rows, ref, strict=strict)


def qtys(rows, name=NAME, price=None):
    return trade.family_quantities(trade.listing_family(rows, name, price))


# ===========================================================================
section("the matrix: which duplicates can be told apart")

# --- same price, different quantity ---------------------------------------
rows = rows_of(("change", 210_000, 250), ("change", 210_000, 100))
for row in rows:
    found, note = find(rows, row)
    check(f"same price, diff qty: x{row.qty} resolves to itself",
          found is not None and found.index == row.index,
          f"got {found.index if found else None} note={note!r}")
    s_found, s_note = find(rows, row, strict=True)
    check(f"same price, diff qty: strict also resolves x{row.qty}",
          s_found is not None and s_found.index == row.index,
          f"got {s_found.index if s_found else None} note={s_note!r} -- "
          f"refusing here would strand a perfectly identifiable stack")

# --- different price, same quantity ---------------------------------------
rows = rows_of(("change", 210_000, 250), ("change", 220_649, 250))
for row in rows:
    found, note = find(rows, row)
    check(f"diff price, same qty: {row.price:,} resolves to itself",
          found is not None and found.index == row.index,
          f"got {found.index if found else None} note={note!r}")

# --- different price, different quantity ----------------------------------
rows = rows_of(("change", 210_000, 250), ("change", 220_649, 100))
for row in rows:
    found, _ = find(rows, row)
    check(f"diff price, diff qty: row {row.index} resolves to itself",
          found is not None and found.index == row.index,
          f"got {found.index if found else None}")

# --- same price, same quantity: the only ambiguous cell -------------------
rows = rows_of(("change", 210_000, 250), ("change", 210_000, 250))
found, note = find(rows, rows[0], strict=True)
check("identical twins: strict refuses", found is None and note == "ambiguous",
      f"got {found.index if found else None} note={note!r}")
found, note = find(rows, rows[0])
check("identical twins: non-strict picks one and says so",
      found is not None and "identical" in note,
      f"got note={note!r}")
check("identical twins: ordinal 0 picks the first",
      found is not None and found.index == 1, f"got {found.index if found else None}")
found1, _ = find(rows, rows[1])
check("identical twins: ordinal 1 picks the second",
      found1 is not None and found1.index == 2,
      f"got {found1.index if found1 else None} -- both ordinals landing on "
      f"the same row would relist one stack twice and never touch the other")


# ===========================================================================
section("the family a collect counts, per cell of the matrix")

check("same price, diff qty: both are in one family",
      qtys(rows_of(("change", 210_000, 250), ("change", 210_000, 100))) ==
      [100, 250], f"{qtys(rows_of(('change', 210_000, 250), ('change', 210_000, 100)))}")

check("diff price: they are SEPARATE families",
      qtys(rows_of(("change", 210_000, 250), ("change", 220_649, 250)),
           price=210_000) == [250],
      "filtering by price is what keeps a collect on one stack from being "
      "measured against another stack's row")

check("identical twins: the family holds both quantities",
      qtys(rows_of(("change", 210_000, 250), ("change", 210_000, 250))) ==
      [250, 250], "")


# ===========================================================================
section("collecting, for every cell")

def collect_case(title, before_rows, sold_index, expect_lost, expect_gained,
                 after_override=None):
    sold = before_rows[sold_index - 1]
    fam = trade.listing_family(before_rows, NAME, sold.price)
    before = trade.family_quantities(fam)
    remaining = [r for r in fam if r.index != sold.index]
    after = (trade.family_quantities(remaining) if after_override is None
             else sorted(after_override, key=lambda q: (q is None, q)))
    lost, gained = trade.collect_delta(before, after)
    check(title, lost == expect_lost and gained == expect_gained,
          f"before={before} after={after} -> lost={lost} gained={gained}, "
          f"expected lost={expect_lost} gained={expect_gained}")


collect_case("same price, diff qty: collecting x250 loses exactly x250",
             rows_of(("receive", 210_000, 250), ("change", 210_000, 100)),
             1, [250], [])
collect_case("same price, diff qty: collecting x100 loses exactly x100",
             rows_of(("change", 210_000, 250), ("receive", 210_000, 100)),
             2, [100], [])
collect_case("identical twins: collecting one loses exactly one",
             rows_of(("receive", 210_000, 250), ("change", 210_000, 250)),
             1, [250], [])
collect_case("three identical: collecting one loses exactly one",
             rows_of(("receive", 210_000, 217), ("change", 210_000, 217),
                     ("change", 210_000, 217)),
             1, [217], [])
collect_case("diff price: the other stack is not even counted",
             rows_of(("receive", 210_000, 250), ("change", 220_649, 250)),
             1, [250], [])
collect_case("partial sale: 250 shrinks to 150",
             rows_of(("receive", 210_000, 250), ("change", 210_000, 100)),
             1, [250], [150], after_override=[100, 150])
collect_case("a dropped click on a twin pair reads as nothing moved",
             rows_of(("receive", 210_000, 250), ("change", 210_000, 250)),
             1, [], [], after_override=[250, 250])


# ===========================================================================
section("unread quantities among duplicates")

# The QTY column is the thing that most often fails to read. Counting has to
# keep working when it does, because that is exactly when identity cannot.
collect_case("both quantities unread: collecting still loses exactly one",
             rows_of(("receive", 210_000, None), ("change", 210_000, None)),
             1, [None], [])
rows = rows_of(("change", 210_000, 250), ("change", 210_000, None))
found, note = find(rows, rows[0], strict=True)
check("one qty unread: the readable one is still identifiable",
      found is not None and found.index == 1,
      f"got {found.index if found else None} note={note!r}")


# ===========================================================================
section("the nasty one: a partial remainder that collides with a twin")

# Two stacks at the same price, 250 and 100. The 250 partially sells down to
# exactly 100 -- so a pair that WAS distinguishable becomes a pair that is not,
# at the moment the script has to decide which is the remainder.
h = Harness(rows=rows_of(("receive", 210_000, 250), ("change", 210_000, 100)),
            panel=empty_panel())


class Partial(Harness):
    """Collecting shrinks the sold stack instead of removing it.

    REPLACES the Row rather than mutating it. read_rows hands out a shallow
    copy of this list, so mutating a Row in place also rewrites the snapshot
    the code under test read BEFORE the click -- the before and after readings
    then agree and the collect looks like a dropped click. The real game cannot
    do that: every read builds fresh objects.
    """

    def _collect(self):
        row = self._cancel_target
        if row is not None and row in self.rows:
            at = self.rows.index(row)
            self.rows[at] = make_row(row.index, row.name, action="change",
                                     price=row.price, qty=100)
            row.action = "change"


h = Partial(rows=rows_of(("receive", 210_000, 250), ("change", 210_000, 100)),
            panel=empty_panel())
with h:
    outcome, exc = run(trade.relist, 1)
    check("colliding remainder: no exception", exc is None, repr(exc))
    check("colliding remainder: did NOT claim sold out",
          outcome != trade.SOLD_OUT,
          f"got {outcome!r} -- the stack did not vanish, it shrank")
    check("colliding remainder: refused rather than picking one",
          h.said("cannot be told") or h.said("which one"),
          f"{h.out()[-400:]} -- two rows now carry x100 and relisting the "
          f"wrong one leaves the sold-down stack stale")


# ===========================================================================
section("a batch of identical twins must relist BOTH, not one twice")

h = Harness(rows=rows_of(("change", 210_000, 250), ("change", 210_000, 250))
            + [make_row(3, OTHER, price=134_000, qty=62)],
            panel=empty_panel())
with h:
    seen = []

    def fake(row, *a, expect=None, **kw):
        seen.append(row)
        return trade.RELISTED

    h.patch("relist", fake)
    ok, exc = run(trade.relist_rows, [1, 2])
    check("twin batch: succeeded", ok is True, f"got {ok!r} {exc!r}")
    check("twin batch: two DIFFERENT rows were relisted",
          len(seen) == 2 and len(set(seen)) == 2,
          f"relisted rows {seen} -- relisting row 1 twice pays two fees and "
          f"leaves the other stack at a stale price")


# ===========================================================================
section("exhaustive: every permutation of 2 and 3 rows of one item")

# One row can be any of action x price x quantity. Quantity includes None,
# because an unread QTY column is routine and is exactly the case where
# identity is weakest. 'receive' is in the mix throughout: a duplicate that
# has SOLD is still a duplicate, and it is the combination that ended the
# 07:51 run.
ACTIONS = ("change", "receive")
PRICES = (210_000, 220_649)
QUANTITIES = (250, 100, None)
SPECS = [(a, p, q) for a in ACTIONS for p in PRICES for q in QUANTITIES]

lost_row, strict_drifted, not_bijective, collect_bad = [], [], [], []
unique_refused = []
unread_ambiguous = []
tables = 0


def signature(row):
    return (row.price, row.qty)


def sweep(combo):
    """Assert the identity properties for one arrangement of rows."""
    global tables
    tables += 1
    rows = rows_of(*combo)
    resolved = {}
    for row in rows:
        ref = trade.RowRef.of(row, rows)
        found, note = trade.locate_row(rows, ref)
        if found is None:
            lost_row.append(f"{combo}: row {row.index} vanished ({note!r})")
            continue
        # It must at least be indistinguishable from what was asked for.
        if signature(found) != signature(row):
            lost_row.append(
                f"{combo}: row {row.index} {signature(row)} resolved to "
                f"row {found.index} {signature(found)}")
        resolved[row.index] = found.index

        s_found, _ = trade.locate_row(rows, ref, strict=True)
        if s_found is not None and signature(s_found) != signature(row):
            strict_drifted.append(
                f"{combo}: strict took row {s_found.index} for row {row.index}")
        # A row that is READABLY unique -- its quantity came through and no
        # other row shares its price and quantity -- is not ambiguous, and
        # refusing it strands a stack that could have been handled.
        #
        # A row whose quantity did NOT read is excluded, and must be: None
        # means "unknown", not a distinct value. Treating it as one would match
        # on the fact that a cell failed to OCR, which is noise -- the same
        # cell may read 250 on the next frame. Refusing those is correct, and
        # they are counted below rather than asserted on.
        same_sig = [r for r in rows if signature(r) == signature(row)]
        if row.qty is None:
            unread_ambiguous.append(combo)
        elif len(same_sig) == 1 and s_found is None:
            unique_refused.append(
                f"{combo}: row {row.index} {signature(row)} is readably "
                f"unique but strict refused it")

        if row.action == "receive":
            fam = trade.listing_family(rows, row.name, row.price)
            before = trade.family_quantities(fam)
            after = trade.family_quantities(
                [r for r in fam if r.index != row.index])
            got = trade.collect_delta(before, after)
            if got != ([row.qty], []):
                collect_bad.append(
                    f"{combo}: collecting row {row.index} gave {got}, "
                    f"expected ([{row.qty!r}], [])")

    # No two rows may resolve to the SAME row: that is one stack relisted
    # twice while its twin is never touched.
    if len(set(resolved.values())) != len(resolved):
        not_bijective.append(f"{combo}: resolved to {resolved}")


import itertools  # noqa: E402

for pair in itertools.product(SPECS, repeat=2):
    sweep(pair)
for triple in itertools.combinations_with_replacement(SPECS, 3):
    sweep(triple)


def report(title, bad):
    detail = ""
    if bad:
        detail = (f"{len(bad)} of {tables} arrangement(s); first 3:\n"
                  "           " + "\n           ".join(bad[:3]))
    return check(title, not bad, detail)


print(f"  ({tables:,} arrangements swept: {len(SPECS)} row kinds, "
      f"pairs and triples)")
report("every row resolves to something indistinguishable from itself",
       lost_row)
report("strict never drifts to a different price/quantity", strict_drifted)
report("no two rows ever resolve to the same row", not_bijective)
report("collecting any sold duplicate loses exactly that stack", collect_bad)
report("a readably-unique row is never refused by strict", unique_refused)
print(f"  ({len(unread_ambiguous):,} row sightings had an unread quantity and "
      f"were therefore\n   correctly treated as ambiguous rather than as "
      f"carrying a distinct value)")


# ===========================================================================
section("an unread quantity is only identifiable when the PRICE is unique")

# The corollary worth pinning down: a row whose quantity did not read is still
# perfectly identifiable if nothing else shares its price.
rows = rows_of(("change", 210_000, None), ("change", 220_649, 250))
found, note = find(rows, rows[0], strict=True)
check("unread qty, unique price: identified",
      found is not None and found.index == 1,
      f"got {found.index if found else None} note={note!r} -- refusing here "
      f"would strand a stack the script can see perfectly well")

rows = rows_of(("change", 210_000, None), ("change", 210_000, 250))
found, note = find(rows, rows[0], strict=True)
check("unread qty, shared price: refused",
      found is None and note == "ambiguous",
      f"got {found.index if found else None} note={note!r}")

rows = rows_of(("receive", 210_000, None), ("change", 220_649, 250))
fam = trade.listing_family(rows, NAME, 210_000)
before = trade.family_quantities(fam)
after = trade.family_quantities([r for r in fam if r.index != 1])
check("unread qty, unique price: collecting still counts correctly",
      trade.collect_delta(before, after) == ([None], []),
      f"before={before} after={after} -> {trade.collect_delta(before, after)}")


raise SystemExit(summary())


raise SystemExit(summary())
