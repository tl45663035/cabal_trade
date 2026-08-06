"""Collecting is decided by the ACTION column, not by the quantities.

From the live run of 2026-08-06 07:57, read out of the recorded table.target
frames rather than the log's prose -- 10 of 10 retried collects looked like
this:

    before (attempt 1): row 10 'Force Core(Highest)' receive x79 @ 190,000
    after  (attempt 2): row 10 'Force Core(Highest)' change  x79 @ 190,000

"Receive" means there are proceeds waiting. It does NOT mean the listing sold
out, and the quantity column shows what is STILL on sale -- which collecting
does not change. Only the action flips.

So for a partial sale the family's quantities are byte-identical either side,
collect_delta returns (lost=[], gained=[]), and the multiset test concluded
"the click did not take" about a collect that had worked. Every time. The
answer was in the same frame, in the column the check never read.

Two costs, and the second is the one that bites: the poll loop waits the whole
TABLE_READ_BUDGET for a quantity change that cannot happen (~40s per collect),
and the attempt is spent, so a row with any further hiccup reaches the attempts
limit and fails outright on "Still sold on the final attempt".

The multiset test is kept and still runs FIRST. It is the only thing that can
tell a fully-sold row from a sibling shifting up into its slot, which is
exactly the duplicate-stack trap this shop hits constantly.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


# The recorded pairs, verbatim from table.target. (row, item, qty, price)
LIVE = [
    (10, "Force Core(Highest)",   79, 190_000),
    (7,  "Force Core (Ultimate)", 150, 381_615),
    (2,  "Force Core(High)",      21, 210_000),
    (5,  "Force Core(High)",       2, 210_000),
    (4,  "Force Core(Medium)",     1, 100_000),
    (4,  "Force Core(Highest)",   44, 200_000),
    (4,  "Force Core(High)",     140, 210_000),
    (8,  "Force Core(High)",     140, 210_000),
    (7,  "Force Core (Ultimate)",  1, 381_614),
    (9,  "Force Core(High)",      30, 210_000),
]


class Collect(Harness):
    """A shop where clicking Receive flips the action and nothing else.

    Which is what the game actually does for a partial sale, and what no test
    modelled before: every previous collect fixture had the row vanish or its
    quantity shrink, so the case that happens most often in production was the
    one case never exercised.
    """

    def __init__(self, table, target_row, flips=True, **kw):
        super().__init__(rows=list(table), panel=empty_panel(), **kw)
        self.target_row = target_row
        self.flips = flips
        self.collects = 0

    def _collect(self):
        """A PARTIAL sale: the proceeds are taken, the rest stays listed.

        Harness._collect removes the row outright, which is the fully-sold
        case. That was the ONLY collect the harness could model, so the
        outcome that dominates this shop in production had no fixture -- which
        is the whole reason the action-column bug survived 26 suites.
        """
        self.collects += 1
        if not self.flips:
            return                        # the click was genuinely dropped
        row = self.rows[self.target_row - 1]
        self.rows[self.target_row - 1] = make_row(
            row.index, row.name, action="change",
            price=row.price, qty=row.qty)


def shop(target_row, name, qty, price, siblings=()):
    """A 10-row table with the target sold and optional identical siblings."""
    table = []
    for i in range(1, 11):
        if i == target_row:
            table.append(make_row(i, name, action="receive",
                                  price=price, qty=qty))
        elif i in siblings:
            table.append(make_row(i, name, action="change",
                                  price=price, qty=qty))
        else:
            table.append(make_row(i, f"Filler {i:02d}",
                                  price=50_000 + i, qty=10 + i))
    return table


# ===========================================================================
section("every recorded live case is read as collected")

for row_no, name, qty, price in LIVE:
    h = Collect(shop(row_no, name, qty, price), row_no)
    with h:
        relisted = {"n": 0}
        h.patch("cancel_item", lambda *a, **k: relisted.__setitem__(
            "n", relisted["n"] + 1) or True)
        h.patch("register_item", lambda *a, **k: True)
        outcome, exc = run(trade.relist, row_no, None, None, False, 8.0, True)

    label = f"row {row_no} {name} x{qty}"
    check(f"{label}: not read as a failed click",
          not h.said("the click did not take"),
          h.out()[-300:])
    check(f"{label}: reported as collected",
          h.said("went from Receive to Change"), h.out()[-300:])
    check(f"{label}: Receive was clicked exactly once", h.collects == 1,
          f"{h.collects} -- a second click would collect a sibling's sale")
    check(f"{label}: the remainder was relisted",
          outcome == trade.RELISTED, f"got {outcome!r} {exc!r}")


# ===========================================================================
section("it does not wait out the budget for a change that cannot come")

# The old loop polled until the QUANTITIES moved. For a partial sale they never
# do, so every collect paid the full TABLE_READ_BUDGET before giving a wrong
# answer. The clock is virtual here, so this measures the polling, not wall time.
h = Collect(shop(3, "Force Core(High)", 140, 210_000), 3)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    start = h.clock.monotonic()
    run(trade.relist, 3, None, None, False, 8.0, True)
    spent = h.clock.monotonic() - start

check("the collect settles well inside the read budget",
      spent < trade.TABLE_READ_BUDGET,
      f"spent {spent:.1f}s of a {trade.TABLE_READ_BUDGET}s budget -- the "
      f"action flips on the first read, so there is nothing to wait for")


# ===========================================================================
section("a click that genuinely did not take is still caught")

h = Collect(shop(4, "Force Core(High)", 140, 210_000), 4, flips=False)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    outcome, exc = run(trade.relist, 4, None, None, False, 8.0, True)

check("a row that stays on Receive is reported as a failed click",
      h.said("the click did not take"), h.out()[-400:])
check("...and says what the row actually shows",
      h.said("still shows 'receive'"), h.out()[-400:])
check("...and does not claim it collected",
      not h.said("went from Receive to Change"), h.out()[-400:])


# ===========================================================================
section("the duplicate-stack trap: a sibling shifting up is NOT my collect")

# Two identical stacks. The target sells out completely, so its row vanishes
# and the sibling shifts up into the same index -- matching on name, price AND
# quantity. Deciding by "the row at my index now says change" would relist the
# sibling and report the sale as a remainder.
#
# The multiset test runs first and sees lost=[q], so it returns SOLD_OUT before
# the action check is ever consulted. This asserts that ordering.
table = shop(4, "Force Core(High)", 140, 210_000, siblings=(5,))


class SoldOut(Collect):
    def _collect(self):
        # Fully sold: the row goes and everything below shifts up, so the
        # identical sibling at row 5 lands on row 4.
        self.collects += 1
        remaining = [r for r in self.rows if r.index != self.target_row]
        self.rows = [make_row(i + 1, r.name, action=r.action,
                              price=r.price, qty=r.qty)
                     for i, r in enumerate(remaining)]


h = SoldOut(table, 4)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    outcome, exc = run(trade.relist, 4, None, None, False, 8.0, True)

check("a fully sold stack is still SOLD_OUT, not a remainder",
      outcome == trade.SOLD_OUT,
      f"got {outcome!r} {exc!r} -- the sibling now sits at row 4 and matches "
      f"on every field; reading that as 'my listing is still here' would "
      f"cancel and relist a stack nobody touched")
check("...and it did not report a Receive->Change collect",
      not h.said("went from Receive to Change"), h.out()[-400:])


# ===========================================================================
section("a different listing at my index is not my collect")

# The case the identity guard actually exists for, and the only one that
# reaches it. Mutating the target's name, price or qty changes the FAMILY
# multiset, so the multiset test fires first and collected() is never
# consulted -- a mutation removing the guard entirely survived those three
# checks untouched. What reaches it is the family staying identical while the
# table reorders under the index: the target is still sold, but it has moved,
# and another listing now sits at row 4.
#
# Without the guard that other listing is read as "my collect, with a
# remainder", and gets cancelled and relisted -- a fee and a new price on a
# stack nobody asked to touch. The same shape as the listing_family bug.
class Reordered(Collect):
    def _collect(self):
        self.collects += 1              # the click landed; the table moved
        target = self.rows[self.target_row - 1]
        rest = [r for r in self.rows if r.index != self.target_row]
        rebuilt = []
        for i, r in enumerate(rest, start=1):
            if i == self.target_row:
                # An unrelated live listing takes the vacated index...
                rebuilt.append(make_row(i, "Unrelated Stack",
                                        action="change", price=99_000, qty=3))
            rebuilt.append(make_row(len(rebuilt) + 1, r.name, action=r.action,
                                    price=r.price, qty=r.qty))
        # ...while the target, still sold, survives further down the table.
        rebuilt.append(make_row(len(rebuilt) + 1, target.name,
                                action="receive", price=target.price,
                                qty=target.qty))
        self.rows = rebuilt


table = shop(4, "Force Core(High)", 140, 210_000)
ref = trade.RowRef.of(table[3], table)

h = Reordered(list(table), 4)
with h:
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    # expect=ref is how relist_rows calls this (trade.py:5735) -- the
    # unattended path always carries the identity forward.
    outcome, exc = run(trade.relist, 4, None, None, False, 8.0, True,
                       trade.RELIST_ATTEMPTS, ref)

check("an unrelated listing at my index is NOT reported as my collect",
      not h.said("went from Receive to Change"),
      f"{h.out()[-600:]}")
check("...and 'Unrelated Stack' is never cancelled or relisted",
      not h.said("row 4: 'Unrelated Stack'"),
      f"cancelling it would charge a fee and reprice a stack nobody touched: "
      f"{h.out()[-600:]}")
check("...it follows the target to its new row instead",
      h.said("moved from row 4") or outcome != trade.FAILED,
      f"got {outcome!r}: {h.out()[-600:]}")


# ===========================================================================
section("the identity fields are checked, not just the slot")

for field, mutate in (
    ("name",  lambda r: make_row(r.index, "Something Else",
                                 action="change", price=r.price, qty=r.qty)),
    ("price", lambda r: make_row(r.index, r.name, action="change",
                                 price=(r.price or 0) + 5_000, qty=r.qty)),
    ("qty",   lambda r: make_row(r.index, r.name, action="change",
                                 price=r.price, qty=(r.qty or 0) + 7)),
):
    class Mutated(Collect):
        def _collect(self, _mutate=mutate):
            self.collects += 1
            self.rows[self.target_row - 1] = _mutate(
                self.rows[self.target_row - 1])

    h = Mutated(shop(6, "Force Core(Highest)", 44, 200_000), 6)
    with h:
        h.patch("cancel_item", lambda *a, **k: True)
        h.patch("register_item", lambda *a, **k: True)
        outcome, exc = run(trade.relist, 6, None, None, False, 8.0, True)

    check(f"a different {field} at the same index is not treated as my collect",
          not h.said("went from Receive to Change"),
          f"{field}: {h.out()[-300:]}")


raise SystemExit(summary())
