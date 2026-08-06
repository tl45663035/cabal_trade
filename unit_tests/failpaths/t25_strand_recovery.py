"""A stranded work tab should clear itself, not end the run.

The failure this replaces, from the live log of 2026-08-06:

    Inventory tab 4 is not empty - 64 slot(s) in use: 1,1, 1,2, ... (+52 more).
    Aborting: the working inventory tab must be empty to start.
    3 cycles have failed in a row - stopping
    Done: 3 cycle(s) run, 0 succeeded, 3 failed

A cancel committed and its re-list did not, so the stack sat in tab IV. The
abort was CORRECT -- with items already in the tab, the before/after diff cannot
tell which slots a new cancel filled -- but nothing in the script ever cleared
it, so every later cycle refused identically until the breaker stopped the run.
This was the oldest known-open finding in t9_outage_replay: "the cause is
recorded now, but the recovery does not exist."

The whole design rests on one asymmetry. An item in an inventory slot CANNOT be
named -- there is no text to read -- so its own floor cannot be looked up, and
pricing it from the market is exactly how a VIP goes out under its floor. So the
recovery never reads the market at all: it lists at the strictest floor on the
books, which is above every floor by construction. That overprices a cheap item,
and that is fine and temporary, because the moment the stack is back in the shop
the ordinary relist path reads its name off the TABLE and re-prices it properly.

Most of this file is that one property, attacked from several directions.
"""
import inspect

from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def live_rows(n=4):
    return [make_row(i, f"Item {i:02d}", price=100_000 + i, qty=50 + i)
            for i in range(1, n + 1)]


class Strand(Harness):
    """A work tab holding a stranded stack that a re-listing clears."""

    def __init__(self, slots=3, clears=True, listing_ok=True, raises=None,
                 **kw):
        super().__init__(**kw)
        self.strand = [(r, c) for r in range(1, 9) for c in range(1, 9)][:slots]
        self.clears = clears
        self.listing_ok = listing_ok
        self.raises = raises
        self.listings: list[dict] = []
        self.priced = 0            # times a market price was consulted

    def install(self):
        super().install()
        h = self
        trade.occupied_slots = lambda *a, **k: list(h.strand)
        trade.require_empty_work_tab = lambda verbose=True: not h.strand
        trade.register_item = self._register
        trade.choose_price = self._choose_price
        return self

    def _register(self, row, col, **kw):
        self.listings.append({"slot": (row, col), **kw})
        self.log("register_item", row, col)
        if self.raises is not None:
            raise self.raises
        if self.listing_ok and self.clears:
            self.strand = []
        return self.listing_ok

    def _choose_price(self, *a, **k):
        self.priced += 1
        return 1, "market"


FLOORS = [floor for *_, floor in trade.ITEM_PRICE_FLOORS]
STRICTEST = trade.strictest_price_floor()


# ===========================================================================
section("the price is a floor, never a market reading")

h = Strand()
with h:
    ok, exc = run(trade.recover_stranded_work_tab)
    check("the strand was re-listed", len(h.listings) == 1,
          f"{len(h.listings)} listing(s) -- one cancelled stack, one listing")
    call = h.listings[0] if h.listings else {}
    price = call.get("force_price")

    check("priced at the strictest floor on the books", price == STRICTEST,
          f"got {price!r}, expected {STRICTEST:,}")
    check("the price is forced, not suggested",
          "force_price" in call,
          "leaving the panel to price it is the market read this avoids")
    check("no market price was consulted", h.priced == 0,
          f"choose_price called {h.priced}x -- an unnameable item priced at "
          f"market is how a VIP goes out under its floor")

    for token, name, floor in trade.ITEM_PRICE_FLOORS:
        check(f"is at or above the {token} floor ({floor:,})",
              price is not None and price >= floor,
              f"{price!r} < {floor:,} would list {name!r} under its floor")

    check("above the plausibility minimum",
          price is not None and price >= trade.MIN_PLAUSIBLE_PRICE,
          f"{price!r}")
    check("not the 10B fallback while real floors exist",
          price != trade.FALLBACK_PRICE or not FLOORS,
          f"{price!r} -- FALLBACK_PRICE is the no-floors-configured case only")

    check("the whole stack goes back, not one unit",
          call.get("maximise_qty") is True,
          f"maximise_qty={call.get('maximise_qty')!r} -- a stranded 250-stack "
          f"re-listed one unit at a time leaves 249 stranded")
    check("returns True once the tab is clear", ok is True, f"{ok!r} {exc!r}")
    check("no exception", exc is None, repr(exc))


# ===========================================================================
section("the floor holds even if the catalogue changes")

# The property is "above every floor", not "equals 180,000,000". Pin it to the
# catalogue rather than to today's numbers, so adding a pricier item cannot
# silently make the recovery underprice it.
saved = trade.ITEM_PRICE_FLOORS
try:
    trade.ITEM_PRICE_FLOORS = list(saved) + [("test", "Costly Thing", 9 * 10**9)]
    h = Strand()
    with h:
        run(trade.recover_stranded_work_tab)
        price = h.listings[0].get("force_price") if h.listings else None
        check("a new, higher floor raises the recovery price",
              price == 9 * 10**9,
              f"got {price!r} -- the price must track the catalogue")

    trade.ITEM_PRICE_FLOORS = []
    h = Strand()
    with h:
        run(trade.recover_stranded_work_tab)
        price = h.listings[0].get("force_price") if h.listings else None
        check("with no floors at all it parks at the fallback",
              price == trade.FALLBACK_PRICE,
              f"got {price!r} -- unsellable for one cycle beats unpriced")
finally:
    trade.ITEM_PRICE_FLOORS = saved

check("the catalogue was restored", trade.ITEM_PRICE_FLOORS is saved, "")


# ===========================================================================
section("an already-clean tab costs nothing")

h = Strand(slots=0)
with h:
    ok, exc = run(trade.recover_stranded_work_tab)
    check("returns True", ok is True, f"{ok!r} {exc!r}")
    check("lists nothing", h.listings == [], f"{h.listings!r}")
    check("does not even try to register",
          "register_item" not in h.names(), f"{h.names()}")


# ===========================================================================
section("it is bounded: a strand that will not clear stops the run")

h = Strand(clears=False)          # every listing "succeeds", tab stays dirty
with h:
    ok, exc = run(trade.recover_stranded_work_tab)
    check("gives up rather than looping forever", ok is False, f"{ok!r}")
    # Independent of the constant on purpose. Asserting
    # `len(listings) == STRAND_RECOVERY_ATTEMPTS` reads the number under test
    # to decide what to expect, so raising the constant to 99 moves the
    # goalposts and the check still passes -- verified: that mutant was MISSED.
    # The bound has a job (stop quickly), so the test states the job.
    check("stops after a handful of attempts, whatever the constant says",
          len(h.listings) <= 5,
          f"{len(h.listings)} attempts -- a strand that will not clear must "
          f"stop the run; retrying it for minutes is worse than the abort "
          f"this replaces, because that at least ended")
    check("the configured bound is small",
          1 <= trade.STRAND_RECOVERY_ATTEMPTS <= 5,
          f"STRAND_RECOVERY_ATTEMPTS={trade.STRAND_RECOVERY_ATTEMPTS}")
    check("it honours the configured bound exactly",
          len(h.listings) == trade.STRAND_RECOVERY_ATTEMPTS,
          f"{len(h.listings)} attempts vs {trade.STRAND_RECOVERY_ATTEMPTS}")
    check("says it stopped rather than kept trying",
          h.said("still in use") or h.said("stopping"), h.out()[-300:])
    check("no exception escaped", exc is None, repr(exc))


# ===========================================================================
section("failure paths fail closed")

h = Strand(listing_ok=False)
with h:
    ok, exc = run(trade.recover_stranded_work_tab)
    check("a refused listing stops immediately", ok is False, f"{ok!r}")
    check("...and does not retry a refusal", len(h.listings) == 1,
          f"{len(h.listings)}")

h = Strand(raises=trade.Aborted("the dialog never came"))
with h:
    ok, exc = run(trade.recover_stranded_work_tab)
    check("an Aborted is caught, not propagated", exc is None, repr(exc))
    check("...and reported as failure", ok is False, f"{ok!r}")
    check("...with the reason", h.said("dialog never came"), h.out()[-200:])

h = Strand(raises=PermissionError("input is blocked"))
with h:
    ok, exc = run(trade.recover_stranded_work_tab)
    check("a blocked input is caught too", exc is None and ok is False,
          f"{ok!r} {exc!r}")

h = Strand()
with h:
    h.patch("inventory_origin", lambda *a, **k: None)
    ok, exc = run(trade.recover_stranded_work_tab)
    check("no Inventory panel: refuses, never guesses a slot",
          ok is False, f"{ok!r}")
    check("...and lists nothing", h.listings == [], f"{h.listings!r}")

h = Strand()
with h:
    h.patch("select_inventory_tab", lambda *a, **k: False)
    ok, exc = run(trade.recover_stranded_work_tab)
    check("cannot reach the work tab: refuses", ok is False, f"{ok!r}")
    check("...and lists nothing from whatever tab IS showing",
          h.listings == [],
          "registering from the wrong tab would list an unrelated item")


# ===========================================================================
section("it works on the tab it is supposed to work on")

h = Strand()
with h:
    run(trade.recover_stranded_work_tab)
    switches = [c[1][0] for c in h.calls if c[0] == "select_inventory_tab"]
    check("selected the work tab", trade.WORK_TAB in switches, f"{switches}")
    check("selected no other tab",
          all(t == trade.WORK_TAB for t in switches), f"{switches}")
    check("listed the first occupied slot",
          h.listings and h.listings[0]["slot"] == (1, 1),
          f"{h.listings[0]['slot'] if h.listings else None}")


# ===========================================================================
section("it leaves evidence")

h = Strand()
with h:
    run(trade.recover_stranded_work_tab)
    ctx = h.rec("strand.recovering")
    check("records that it recovered", ctx is not None, f"{h.labels()}")
    if ctx:
        check("records the tab", ctx.get("tab") == trade.WORK_TAB, f"{ctx}")
        check("records how much was stranded", ctx.get("occupied") == 3, f"{ctx}")
        check("records the price it used", ctx.get("price") == STRICTEST, f"{ctx}")
        check("records the slot", ctx.get("slot") == "1,1", f"{ctx}")


# ===========================================================================
section("ensure_work_tab_empty: recover, then verify")

h = Strand(slots=0)
with h:
    calls = {"n": 0}
    h.patch("recover_stranded_work_tab",
            lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or True)
    ok, exc = run(trade.ensure_work_tab_empty)
    check("a clean tab never invokes recovery", calls["n"] == 0,
          f"called {calls['n']}x")
    check("...and passes", ok is True, f"{ok!r} {exc!r}")

h = Strand()
with h:
    ok, exc = run(trade.ensure_work_tab_empty)
    check("a strand is cleared and the check then passes", ok is True,
          f"{ok!r} {exc!r}")
    check("said what it was doing", h.said("never got re-listed"),
          h.out()[-400:])

h = Strand(clears=False)
with h:
    ok, exc = run(trade.ensure_work_tab_empty)
    check("an unclearable strand still fails the precondition", ok is False,
          f"{ok!r} -- failing open here is what the empty-tab rule exists to "
          f"prevent")

# Recovery claiming success is not enough; the tab is re-read afterwards.
h = Strand(clears=False)
with h:
    h.patch("recover_stranded_work_tab", lambda *a, **k: True)
    ok, exc = run(trade.ensure_work_tab_empty)
    check("a lying recovery does not get to skip the re-check", ok is False,
          f"{ok!r} -- the precondition is the tab being empty, not a function "
          f"saying it is")


# ===========================================================================
section("the batch that used to die now runs")

h = Strand(rows=live_rows(), panel=empty_panel())
with h:
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1, 2])
    check("relist_rows no longer aborts on a stranded tab", ok is True,
          f"{ok!r} {exc!r} -- this is the 3-failed-cycles log verbatim")
    check("...and did not print the old abort",
          not h.said("must be empty to start"), h.out()[-400:])
    check("...having cleared the strand first", h.strand == [], f"{h.strand}")

h = Strand(clears=False, rows=live_rows(), panel=empty_panel())
with h:
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1, 2])
    check("an unclearable strand still aborts the batch", ok is False,
          f"{ok!r} {exc!r}")
    check("...with the original message", h.said("must be empty to start"),
          h.out()[-400:])


# ===========================================================================
section("mid-batch, a dirty tab is still a failure to report")

# Recovery belongs at the START of a batch, where a dirty tab means a PREVIOUS
# run died. Mid-batch it means the row just relisted stranded something, and
# auto-clearing that would hide the very failure the check exists to catch.
src = inspect.getsource(trade.relist_rows)
check("relist_rows still calls require_empty_work_tab mid-batch",
      "require_empty_work_tab(verbose=False)" in src,
      "the mid-batch progress checks must not auto-recover")
check("relist_rows uses the recovering variant exactly once",
      src.count("ensure_work_tab_empty(") == 1,
      f"{src.count('ensure_work_tab_empty(')} call sites -- only the opening "
      f"precondition should recover")

h = Strand(slots=0, rows=live_rows(), panel=empty_panel())
with h:
    calls = {"n": 0}

    def spy(*a, **k):
        calls["n"] += 1
        h.strand = []
        return True

    h.patch("recover_stranded_work_tab", spy)

    def strand_after_row(*a, **k):
        h.strand = [(1, 1)]        # this row leaves something behind
        return trade.FAILED

    h.patch("relist", strand_after_row)
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("a row that strands something stops the batch", ok is False,
          f"{ok!r} {exc!r}")
    check("recovery did not run mid-batch", calls["n"] == 0,
          f"called {calls['n']}x -- clearing a strand the current batch just "
          f"created would turn a reportable failure into a silent one")


raise SystemExit(summary())
