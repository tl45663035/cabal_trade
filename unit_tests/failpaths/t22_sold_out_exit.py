"""A sold-out shop should finish the run, not cycle over nothing.

Before this, a shop whose rows were all empty slots returned a "successful"
cycle having relisted nothing, and --repeat kept doing that for the rest of its
500 minutes: no work, the machine held awake, the cursor moving, and the moment
the shop actually sold out buried in hours of identical log.

ShopEmpty is deliberately NOT an Aborted subclass. Aborted means "this cycle
did not work, try again"; this means "the work is finished". It has to reach
run_loop past run_sequence's `except Aborted` and past run_loop's own catch-all
`except Exception`, so the ordering of those handlers is asserted here rather
than assumed.

The other half is leaving the game tidy. A run that stops with the Trade window
open parks the character in a UI, and an open Trade window is exactly what makes
a later find_npc fail, because it covers the NPC.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def empty_rows(n=10):
    """A table of nothing but empty Register slots."""
    return [make_row(i, "(empty)", action="register", price=None, qty=None)
            for i in range(1, n + 1)]


def live_rows(n=3):
    return [make_row(i, f"Item {i:02d}", price=100_000 + i, qty=50 + i)
            for i in range(1, n + 1)]


# ===========================================================================
section("an all-empty shop raises ShopEmpty rather than looping")

h = Harness(rows=empty_rows(), panel=empty_panel())
with h:
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("raises ShopEmpty", isinstance(exc, trade.ShopEmpty),
          f"got {exc!r} / returned {ok!r}")
    check("ShopEmpty is not an Aborted",
          not isinstance(exc, trade.Aborted) if exc else False,
          "run_sequence catches Aborted; inheriting from it would turn "
          "'finished' into 'retry this cycle' and the loop would never stop")
    check("says the shop sold out",
          exc is not None and "sold out" in str(exc), f"{exc!r}")


# ===========================================================================
section("one bad frame must not end the run")

# "Every row is empty" is also what a table caught mid-refresh looks like.
class Flaky(Harness):
    """The first read is empty; the re-read shows the shop is fine."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.reads = 0
        self.real_rows = live_rows()

    def _read_rows(self, source=None):
        self.reads += 1
        # 1st read: the batch snapshot, empty. Later: the truth.
        return empty_rows() if self.reads <= 1 else list(self.real_rows)


h = Flaky(rows=live_rows(), panel=empty_panel())
with h:
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("a mid-refresh frame does NOT raise ShopEmpty",
          not isinstance(exc, trade.ShopEmpty),
          f"got {exc!r} -- ending a 500-minute run on one bad frame is worse "
          f"than the pointless cycling this avoids")
    check("...and says the re-read disagreed",
          h.said("after all") or h.said("mid-refresh"), h.out()[-300:])


# ===========================================================================
section("a shop with ANY live row carries on as normal")

h = Harness(rows=live_rows() + empty_rows(3), panel=empty_panel())
with h:
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("no ShopEmpty when work exists",
          not isinstance(exc, trade.ShopEmpty), f"{exc!r}")
    check("batch succeeded", ok is True, f"got {ok!r} {exc!r}")


# ===========================================================================
section("run_loop stops, counts it a success, and closes the shop")

h = Harness(rows=empty_rows(), panel=empty_panel())
with h:
    cycles = {"n": 0}

    def sold_out(*a, **k):
        cycles["n"] += 1
        raise trade.ShopEmpty("every row is an empty slot - the shop has "
                              "sold out")

    h.patch("run_sequence", sold_out)
    h.patch("prepare_for_actions", lambda *a, **k: True)
    h.trade_open = True
    ok, exc = run(trade.run_loop, ["relist-rows 1-10"], 500.0, 0.0)

    check("loop stopped after ONE cycle", cycles["n"] == 1,
          f"ran {cycles['n']} cycles -- the point is not to keep going")
    check("loop reports success", ok is True,
          f"got {ok!r} -- a sold-out shop is the job finished, and a caller "
          f"scripting this must not read it as a failure")
    check("said SOLD OUT", h.said("SOLD OUT"), h.out()[-400:])
    check("no exception escaped", exc is None, repr(exc))
    check("the Trade window was closed", h.trade_open is False,
          "a run that stops with the shop open leaves the character in a UI, "
          "and an open Trade window covers the NPC for the next run")


# ===========================================================================
section("leave_shop tidies whatever it finds")

h = Harness(rows=live_rows(), panel=empty_panel(), dialog="confirm")
with h:
    h.trade_open = True
    ok, exc = run(trade.leave_shop)
    check("closes a dialog that is open", h.dialog is None,
          f"dialog still {h.dialog!r}")
    check("closes the Trade window", h.trade_open is False, "")
    check("reports success", ok is True, f"got {ok!r} {exc!r}")

h = Harness(rows=live_rows(), panel=empty_panel())
with h:
    h.trade_open = False
    ok, exc = run(trade.leave_shop)
    check("already tidy: still succeeds, no exception",
          ok is True and exc is None, f"{ok!r} {exc!r}")

# It runs at the very end of a run whose outcome is already decided, so an
# exception here must never replace that outcome.
h = Harness(rows=live_rows(), panel=empty_panel())
with h:
    h.patch("trade_window_open",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    ok, exc = run(trade.leave_shop)
    check("never raises, even when the probe explodes",
          exc is None and ok is False, f"{ok!r} {exc!r}")
    check("...and says what went wrong", h.said("could not tidy"),
          h.out()[-200:])


raise SystemExit(summary())
