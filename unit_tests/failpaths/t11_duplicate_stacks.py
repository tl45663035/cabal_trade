from harness import (Harness, RECEIPT_XY, check, make_row, run, section,
                     summary)

import trade


def receipts(h):
    n = 0
    for name, args, _ in h.calls:
        if name == "click" and len(args) >= 2:
            if (abs(args[0] - RECEIPT_XY[0]) <= 40
                    and abs(args[1] - RECEIPT_XY[1]) <= 40):
                n += 1
    return n


def sold_pair(qty=217, price=210_000, count=2, name="Force Core(High)"):
    return [make_row(i, name, action="receive", price=price, qty=qty)
            for i in range(1, count + 1)]


section("two identical sold stacks -- collecting one must not collect the other")

with Harness(rows=sold_pair(count=2)) as h:
    outcome, exc = run(trade.relist, 1, verbose=False)
    check("2 stacks: no exception", exc is None, repr(exc))
    check("2 stacks: reports sold out", outcome == trade.SOLD_OUT,
          f"got {outcome!r}")
    check("2 stacks: exactly ONE Receive accepted", receipts(h) == 1,
          f"clicked Receive {receipts(h)} time(s) -- more than one means a "
          f"second stack was pulled off the market for a single sale")
    check("2 stacks: the sibling is still listed", len(h.rows) == 1,
          f"{len(h.rows)} row(s) left, expected 1")
    check("2 stacks: never claimed the click failed",
          not h.said("did not take"), h.out()[-400:])


section("three identical sold stacks -- must not strand the collected item")

with Harness(rows=sold_pair(count=3)) as h:
    outcome, exc = run(trade.relist, 1, verbose=False)
    check("3 stacks: no exception", exc is None, repr(exc))
    check("3 stacks: reports sold out", outcome == trade.SOLD_OUT,
          f"got {outcome!r}")
    check("3 stacks: exactly ONE Receive accepted", receipts(h) == 1,
          f"clicked Receive {receipts(h)} time(s)")
    check("3 stacks: did NOT bail out as ambiguous",
          not h.said("cannot be told apart"),
          "ambiguity here strands the collected item in the work tab and "
          "every later cycle fails its empty-tab check")
    check("3 stacks: two siblings still listed", len(h.rows) == 2,
          f"{len(h.rows)} row(s) left, expected 2")


section("a genuinely dropped click must still retry")

class Stubborn(Harness):

    def _collect(self):
        return


with Stubborn(rows=sold_pair(count=2)) as h:
    outcome, exc = run(trade.relist, 1, verbose=False)
    check("dropped click: no exception", exc is None, repr(exc))
    check("dropped click: does NOT report sold out",
          outcome != trade.SOLD_OUT,
          "nothing was collected, so claiming sold-out loses a live stack")
    check("dropped click: retried rather than giving up silently",
          receipts(h) > 1 or h.said("did not take"),
          f"receipts={receipts(h)}; out={h.out()[-300:]}")
    check("dropped click: both stacks still listed", len(h.rows) == 2,
          f"{len(h.rows)} row(s) left, expected 2")


section("a lone sold stack still collects exactly once")

lone = sold_pair(count=1) + [make_row(2, "Upgrade Core(Highest)",
                                      price=134_000, qty=62)]
with Harness(rows=lone) as h:
    outcome, exc = run(trade.relist, 1, verbose=False)
    check("single: reports sold out", outcome == trade.SOLD_OUT,
          f"got {outcome!r}")
    check("single: exactly ONE Receive accepted", receipts(h) == 1,
          f"clicked Receive {receipts(h)} time(s)")
    check("single: only the unrelated row is left", len(h.rows) == 1,
          f"{len(h.rows)} row(s) left, expected 1")
    check("single: the unrelated row was untouched",
          h.rows and h.rows[0].name == "Upgrade Core(Highest)",
          f"left {[r.name for r in h.rows]}")


section("unreadable quantities must not defeat the count")

with Harness(rows=sold_pair(count=2, qty=None)) as h:
    outcome, exc = run(trade.relist, 1, verbose=False)
    check("qty unread: no exception", exc is None, repr(exc))
    check("qty unread: reports sold out", outcome == trade.SOLD_OUT,
          f"got {outcome!r} -- counting works without the QTY column, which "
          f"is exactly when identity matching was weakest")
    check("qty unread: exactly ONE Receive accepted", receipts(h) == 1,
          f"clicked Receive {receipts(h)} time(s)")


raise SystemExit(summary())
