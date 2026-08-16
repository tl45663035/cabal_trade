"""Bug 3: collecting a sale when the shop holds other stacks of the same item.

The failure that ended the 07:51 run:

    2 rows are identical ('Force Core(High)' x217 at 210,000 Alz)
    Row 3 is sold - clicking Receive.
    Row 4 still shows Receive - the click did not take; retrying.
    Row 4 is sold - clicking Receive.            <- a SECOND stack collected

The old code asked "is my listing still there?" and answered it by identity.
With a twin present that question has no answer, and the survivor matching
perfectly was read as proof the click had failed.

Swept here across the shapes a family can take: how many stacks, whether their
quantities are equal, distinct, partly equal or unreadable, whether they share
a price, and which of them sold. The property is the same in every one:

    collecting ONE sale accepts exactly ONE receipt, and leaves every other
    stack listed.

Anything else is either a second stack pulled off the market for one sale, or a
sold stack reported as still listed.
"""
import itertools

from harness import (Harness, RECEIPT_XY, REFRESH_XY, check, empty_panel,
                     make_row, run, section, summary)

import trade

NAME = "Force Core(High)"
FILLER = "Upgrade Core(Highest)"
BASE_PRICE = 210_000
ALT_PRICE = 220_649


def receipts(h):
    """How many times the Confirm Receipt accept button was clicked."""
    return sum(1 for name, args, _ in h.calls
               if name == "click" and len(args) >= 2
               and abs(args[0] - RECEIPT_XY[0]) <= 40
               and abs(args[1] - RECEIPT_XY[1]) <= 40)


def quantities(n, mode):
    if mode == "same":
        return [217] * n
    if mode == "distinct":
        return [250, 100, 60, 33][:n]
    if mode == "pair":
        return ([217, 217] + [100, 60][:max(0, n - 2)])[:n]
    if mode == "none":
        return [None] * n
    raise AssertionError(mode)


def prices(n, mode):
    if mode == "same":
        return [BASE_PRICE] * n
    # Alternate, so the sold stack sometimes shares its price and sometimes
    # does not -- the family filter is by price, so this decides whether the
    # twins are even counted together.
    return [BASE_PRICE if i % 2 == 0 else ALT_PRICE for i in range(n)]


def build(n, qty_mode, price_mode, sold_at):
    qs, ps = quantities(n, qty_mode), prices(n, price_mode)
    rows = [make_row(i + 1, NAME,
                     action="receive" if i + 1 == sold_at else "change",
                     price=ps[i], qty=qs[i])
            for i in range(n)]
    # A real shop is never a bare family: without another listing, collecting
    # the last row leaves an empty table, which read_rows reports as
    # unreadable rather than as an empty shop -- correctly, and it would mask
    # what this is measuring.
    rows.append(make_row(n + 1, FILLER, price=134_000, qty=62))
    return rows


# ===========================================================================
section("every family shape: one sale, one receipt, every twin left listed")

N = (1, 2, 3, 4)
QTY_MODES = ("same", "distinct", "pair", "none")
PRICE_MODES = ("same", "split")

swept = 0
for n, qty_mode, price_mode in itertools.product(N, QTY_MODES, PRICE_MODES):
    if n < 2 and qty_mode in ("pair",):
        continue                      # a pair needs two rows
    for sold_at in sorted({1, n}):
        swept += 1
        label = f"n{n} {qty_mode:8}/{price_mode:5} sold@{sold_at}"
        h = Harness(rows=build(n, qty_mode, price_mode, sold_at),
                    panel=empty_panel())
        with h:
            outcome, exc = run(trade.relist, sold_at)
            got = receipts(h)
            check(f"{label}: exactly one receipt, reported sold out",
                  exc is None and outcome == trade.SOLD_OUT and got == 1,
                  f"outcome={outcome!r} receipts={got} exc={exc!r} -- more "
                  f"than one receipt is a second stack pulled off the market "
                  f"for a single sale")
            check(f"{label}: every other stack still listed",
                  len(h.rows) == n,
                  f"{len(h.rows)} row(s) left of {n} expected "
                  f"({[r.qty for r in h.rows]})")

print(f"  ({swept} family shapes swept)")


# ===========================================================================
section("a genuinely dropped click must still look dropped")

class Stubborn(Harness):
    """The receipt is accepted and the game does nothing."""

    def _collect(self):
        return


dropped = 0
for n, qty_mode in itertools.product((1, 2, 3), ("same", "distinct", "none")):
    dropped += 1
    label = f"dropped n{n} {qty_mode:8}"
    h = Stubborn(rows=build(n, qty_mode, "same", 1), panel=empty_panel())
    with h:
        outcome, exc = run(trade.relist, 1)
        check(f"{label}: never claimed sold out",
              outcome != trade.SOLD_OUT,
              f"got {outcome!r} -- nothing was collected, so reporting the "
              f"stack gone abandons a live listing")
        check(f"{label}: every stack still listed", len(h.rows) == n + 1,
              f"{len(h.rows)} row(s), expected {n + 1}")
        check(f"{label}: no exception", exc is None, repr(exc))

print(f"  ({dropped} dropped-click shapes swept)")


# ===========================================================================
section("partial sales, including a remainder that collides with a twin")

def partial_case(n, qty_mode, remainder):
    class Partial(Harness):
        def _collect(self):
            row = self._cancel_target
            if row is not None and row in self.rows:
                at = self.rows.index(row)
                # REPLACE, never mutate: read_rows hands out a shallow copy,
                # so mutating in place also rewrites the snapshot taken before
                # the click and the two readings agree by accident.
                self.rows[at] = make_row(row.index, row.name, action="change",
                                         price=row.price, qty=remainder)

    return Partial(rows=build(n, qty_mode, "same", 1), panel=empty_panel())


# A remainder that is unique in the family: identifiable, so it may proceed.
h = partial_case(2, "distinct", 77)
with h:
    outcome, exc = run(trade.relist, 1)
    check("partial, unique remainder: did not report sold out",
          outcome != trade.SOLD_OUT, f"got {outcome!r}")
    check("partial, unique remainder: named the remainder",
          h.said("Partially sold") or h.said("relisting the remainder"),
          h.out()[-400:])

# A remainder equal to an existing twin: no longer identifiable, so it must
# refuse rather than relist an arbitrary one of the two.
h = partial_case(2, "distinct", 100)      # the other stack is already x100
with h:
    outcome, exc = run(trade.relist, 1)
    check("partial, colliding remainder: refused",
          outcome == trade.FAILED, f"got {outcome!r}")
    check("partial, colliding remainder: said which decision it could not make",
          h.said("cannot be told") or h.said("which one"), h.out()[-400:])
    check("partial, colliding remainder: no exception", exc is None, repr(exc))


# ===========================================================================
section("two stacks BOTH sold: collecting one must not collect the other")

for qty_mode in ("same", "distinct", "none"):
    rows = [make_row(1, NAME, action="receive", price=BASE_PRICE,
                     qty=quantities(2, qty_mode)[0]),
            make_row(2, NAME, action="receive", price=BASE_PRICE,
                     qty=quantities(2, qty_mode)[1]),
            make_row(3, FILLER, price=134_000, qty=62)]
    h = Harness(rows=rows, panel=empty_panel())
    with h:
        outcome, exc = run(trade.relist, 1)
        check(f"both sold, {qty_mode:8}: exactly one receipt",
              receipts(h) == 1, f"{receipts(h)} receipts")
        check(f"both sold, {qty_mode:8}: the other sale is still there",
              len(h.rows) == 2 and any(r.action == "receive" for r in h.rows),
              f"rows left: {[(r.index, r.action, r.qty) for r in h.rows]} -- "
              f"the second sale is next cycle's work, not this one's")


# ===========================================================================
section("the collect decision in isolation, over the same shapes")

# Same shapes again, straight through the real functions rather than through
# relist(), so a failure separates "the decision is wrong" from "the sequence
# around it is wrong".
bad = []
for n, qty_mode, price_mode in itertools.product(N, QTY_MODES, PRICE_MODES):
    if n < 2 and qty_mode == "pair":
        continue
    for sold_at in sorted({1, n}):
        rows = build(n, qty_mode, price_mode, sold_at)
        sold = rows[sold_at - 1]
        fam = trade.listing_family(rows, NAME, sold.price)
        before = trade.family_quantities(fam)
        after = trade.family_quantities(
            [r for r in fam if r.index != sold.index])
        lost, gained = trade.collect_delta(before, after)
        if not (lost == [sold.qty] and gained == []):
            bad.append(f"n{n} {qty_mode}/{price_mode} sold@{sold_at}: "
                       f"before={before} after={after} lost={lost} "
                       f"gained={gained}")
check("collect_delta reports exactly one stack lost, in every shape",
      not bad,
      f"{len(bad)} shape(s); first 3:\n           "
      + "\n           ".join(bad[:3]))


# ===========================================================================
section("the table must be REFETCHED before the collect is counted")

# wait_for_table waits for a reload to finish; it does not cause one. Without
# an explicit refresh the count reads the client's stale copy, which still
# shows the pre-sale quantity however long it is polled.
#
# Measured on the 08:27 run: 16 collects polled the full 45s budget, reported
# "the click did not take", and retried -- and on the retry, which reopens the
# shop and therefore refreshes, the row already showed the collected quantity
# gone. Every one of those collects had worked.
h = Harness(rows=build(2, "distinct", "same", 1), panel=empty_panel())
with h:
    outcome, exc = run(trade.relist, 1)
    # refresh_table is not stubbed -- it runs for real and clicks the Refresh
    # button the harness reports, so the evidence is the click itself.
    refreshes = sum(1 for n, args, _ in h.calls
                    if n == "click" and len(args) >= 2
                    and abs(args[0] - REFRESH_XY[0]) <= 40
                    and abs(args[1] - REFRESH_XY[1]) <= 40)
    check("the Refresh button is clicked after the receipt is accepted",
          refreshes >= 1,
          f"{refreshes} Refresh click(s) -- without one the count polls the "
          f"client's stale copy and a working collect reads as a dropped "
          f"click")
    check("the collect is still reported correctly",
          outcome == trade.SOLD_OUT and exc is None,
          f"outcome={outcome!r} exc={exc!r}")
    check("exactly one receipt, unchanged by the refresh", receipts(h) == 1,
          f"{receipts(h)} receipts")

# And the message must describe what was measured, not assert screen state it
# never read.
h = Stubborn(rows=build(2, "distinct", "same", 1), panel=empty_panel())
with h:
    run(trade.relist, 1)
    check("a genuine no-op says the listings are unchanged",
          h.said("are unchanged after collecting"), h.out()[-400:])
    check("...and does NOT claim the row still shows Receive",
          not h.said("still shows Receive"),
          "that wording was never checked against the screen, and was wrong "
          "every time it printed during the 08:27 run")


raise SystemExit(summary())
