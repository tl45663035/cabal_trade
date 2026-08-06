"""A shop that has consolidated should be walked as 1-K, not 1-24.

Listings consolidate upward, and RELISTING drives it, not sales. Cancelling
frees a slot and the re-registration lands in the LOWEST empty one, so each
cycle pulls listings toward the top and pushes empties to the bottom. Measured
on the 07:57 run of 2026-08-06: "Siena's Unbinding Stone" went row 24 -> 17 ->
12 over three cycles. A run started as `relist-rows 1-24` on a shop holding 19
listings converges to 1-19.

The convergence is gradual, which is the trap: partway through, the empties are
scattered mid-table with live listings still below them.

Two things this must not do, and they pull in opposite directions:

  * trim a GAP. An empty row higher up is a transient reading -- a row caught
    mid-collect, a name column that failed to OCR -- and skipping it would stop
    relisting a live listing with no message saying so.
  * trim when EVERYTHING is empty. The sold-out check after the loop needs the
    full target list to tell "the shop is finished" (ShopEmpty, stop the run,
    exit 0) from "this batch happened to have nothing in it".

So: only the tail, and only when something live precedes it.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def table(live, total=24, gaps=()):
    """`live` live rows at the top, empties after; `gaps` are empty mid-table."""
    out = []
    for i in range(1, total + 1):
        if i in gaps or i > live:
            out.append(make_row(i, "(empty)", action="register",
                                price=None, qty=None))
        else:
            out.append(make_row(i, f"Force Core {i:02d}",
                                price=100_000 + i, qty=10 + i))
    return out


class Shop(Harness):
    """Tracks which rows the batch actually walked."""

    def __init__(self, rows, **kw):
        super().__init__(rows=list(rows), panel=empty_panel(), **kw)
        self.walked: list[int] = []

    def install(self):
        super().install()
        h = self
        # enumerate_listings is what a 1-24 batch uses; give it the whole table.
        trade.enumerate_listings = lambda *a, **k: [
            (r.index, r) for r in h.rows]
        return self


def walk(shop_rows, spec):
    h = Shop(shop_rows)
    with h:
        def relist(row, *a, **k):
            h.walked.append(row)
            return trade.RELISTED
        h.patch("relist", relist)
        ok, exc = run(trade.relist_rows, spec)
    return h, ok, exc


ALL24 = list(range(1, 25))


# ===========================================================================
section("the reported case: 24 asked for, 18 still live")

h, ok, exc = walk(table(live=18), ALL24)
check("the batch succeeded", ok is True, f"{ok!r} {exc!r}")
check("it says the shop consolidated", h.said("has consolidated"),
      h.out()[:800])
check("it names the new range", h.said("rows 1-18 instead"), h.out()[:800])
check("only the live rows were walked", h.walked == list(range(1, 19)),
      f"walked {h.walked}")
check("no dead slot was visited", max(h.walked or [0]) == 18,
      f"highest row walked: {max(h.walked or [0])}")
check("the trim is recorded", h.rec("batch.trimmed") is not None,
      f"{h.labels()}")
ctx = h.rec("batch.trimmed") or {}
check("...with what was kept and dropped",
      ctx.get("kept") == 18 and ctx.get("dropped") == 6, f"{ctx}")
check("...and where the empties start", ctx.get("first_empty") == 19, f"{ctx}")


# ===========================================================================
section("nothing to trim leaves the batch alone")

h, ok, exc = walk(table(live=24), ALL24)
check("a full shop walks all 24", h.walked == ALL24, f"walked {h.walked}")
check("...and says nothing about consolidating",
      not h.said("has consolidated"), h.out()[:400])
check("...and records no trim", h.rec("batch.trimmed") is None, f"{h.labels()}")


# ===========================================================================
section("a gap in the middle is NOT trimmed")

# Row 7 empty, rows 1-18 otherwise live. The batch must still reach rows 8-18.
h, ok, exc = walk(table(live=18, gaps=(7,)), ALL24)
check("rows past the gap are still walked", 18 in h.walked, f"{h.walked}")
check("the gap itself is skipped, not the tail after it",
      7 not in h.walked and sorted(h.walked) == [i for i in range(1, 19)
                                                 if i != 7],
      f"walked {h.walked}")
check("the range still reports 1-18", h.said("rows 1-18 instead"),
      h.out()[:800])

# A gap immediately before the live tail is the sharpest version: trimming at
# the FIRST empty rather than the LAST live one would cut rows 18-20 off.
h, ok, exc = walk(table(live=20, gaps=(17, 18)), ALL24)
check("two gaps do not truncate the live rows after them",
      19 in h.walked and 20 in h.walked, f"walked {h.walked}")
check("...and the batch is 1-20", h.said("rows 1-20 instead"), h.out()[:800])


# ===========================================================================
section("one live row left")

h, ok, exc = walk(table(live=1), ALL24)
check("the batch shrinks to a single row", h.walked == [1], f"{h.walked}")
check("...and says so", h.said("rows 1-1 instead"), h.out()[:800])


# ===========================================================================
section("an entirely empty shop is still SOLD OUT, not a trimmed batch")

# The trim must not consume this case. With every slot empty there is no live
# row to trim back to, so the full list survives to the sold-out check -- which
# is what stops a 500-minute run instead of cycling forever on nothing.
h = Shop(table(live=0))
with h:
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, ALL24)

check("ShopEmpty is raised", isinstance(exc, trade.ShopEmpty),
      f"got {exc!r} / returned {ok!r} -- trimming to an empty target list "
      f"would turn 'the shop sold out' into 'this batch did nothing'")
check("...and it counted all 24 slots", exc is not None and "24" in str(exc),
      f"{exc!r}")
check("...and nothing was walked", h.walked == [], f"{h.walked}")


# ===========================================================================
section("it is not a ratchet: relisting into the empties grows it back")

# The range is recomputed from a fresh read each cycle, so a shop that refills
# must widen again. A trim that persisted across cycles would quietly cap the
# shop at whatever its emptiest moment was.
shop_rows = table(live=18)
h, _, _ = walk(shop_rows, ALL24)
check("first pass is 1-18", max(h.walked) == 18, f"{h.walked}")

shop_rows = table(live=22)          # two new listings added since
h, _, _ = walk(shop_rows, ALL24)
check("a refilled shop widens back to 1-22", max(h.walked) == 22,
      f"walked {h.walked} -- the trim is derived per cycle, never remembered")


# ===========================================================================
section("a narrower request is still honoured")

# Asking for 1-10 of an 18-live shop must stay 1-10: the trim removes dead
# slots, it does not expand the batch to everything live.
h, ok, exc = walk(table(live=18), list(range(1, 11)))
check("rows 1-10 stay rows 1-10", h.walked == list(range(1, 11)),
      f"walked {h.walked}")
check("...with no trim message", not h.said("has consolidated"),
      h.out()[:400])


raise SystemExit(summary())
