from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def table(live, total=24, gaps=()):
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

    def __init__(self, rows, **kw):
        super().__init__(rows=list(rows), panel=empty_panel(), **kw)
        self.walked: list[int] = []

    def install(self):
        super().install()
        h = self
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


section("nothing to trim leaves the batch alone")

h, ok, exc = walk(table(live=24), ALL24)
check("a full shop walks all 24", h.walked == ALL24, f"walked {h.walked}")
check("...and says nothing about consolidating",
      not h.said("has consolidated"), h.out()[:400])
check("...and records no trim", h.rec("batch.trimmed") is None, f"{h.labels()}")


section("a gap in the middle is NOT trimmed")

h, ok, exc = walk(table(live=18, gaps=(7,)), ALL24)
check("rows past the gap are still walked", 18 in h.walked, f"{h.walked}")
check("the gap itself is skipped, not the tail after it",
      7 not in h.walked and sorted(h.walked) == [i for i in range(1, 19)
                                                 if i != 7],
      f"walked {h.walked}")
check("the range still reports 1-18", h.said("rows 1-18 instead"),
      h.out()[:800])

h, ok, exc = walk(table(live=20, gaps=(17, 18)), ALL24)
check("two gaps do not truncate the live rows after them",
      19 in h.walked and 20 in h.walked, f"walked {h.walked}")
check("...and the batch is 1-20", h.said("rows 1-20 instead"), h.out()[:800])


section("one live row left")

h, ok, exc = walk(table(live=1), ALL24)
check("the batch shrinks to a single row", h.walked == [1], f"{h.walked}")
check("...and says so", h.said("rows 1-1 instead"), h.out()[:800])


section("an entirely empty shop is still SOLD OUT, not a trimmed batch")

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


section("it is not a ratchet: relisting into the empties grows it back")

shop_rows = table(live=18)
h, _, _ = walk(shop_rows, ALL24)
check("first pass is 1-18", max(h.walked) == 18, f"{h.walked}")

shop_rows = table(live=22)
h, _, _ = walk(shop_rows, ALL24)
check("a refilled shop widens back to 1-22", max(h.walked) == 22,
      f"walked {h.walked} -- the trim is derived per cycle, never remembered")


section("a narrower request is still honoured")

h, ok, exc = walk(table(live=18), list(range(1, 11)))
check("rows 1-10 stay rows 1-10", h.walked == list(range(1, 11)),
      f"walked {h.walked}")
check("...with no trim message", not h.said("has consolidated"),
      h.out()[:400])


raise SystemExit(summary())
