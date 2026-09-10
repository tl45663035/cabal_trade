from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def rows(n=6):
    return [make_row(i, f"Force Core {i:02d}", price=100_000 + i, qty=10 + i)
            for i in range(1, n + 1)]


def fresh_session():
    trade.note_shop_closed()


def faithful_open(h):
    def _open(timeout=15.0, verbose=True):
        h.log("open_trade_window")
        h.trade_open = True
        trade.note_shop_opened()
        return True
    return _open





section("the session clock")

fresh_session()
check("no session means expired", trade.shop_session_expired() is True,
      "an unknown state must close and start clean, never stay open")
check("...and has no age", trade.shop_session_age() is None,
      f"{trade.shop_session_age()!r}")

h = Harness(rows=rows(), panel=empty_panel())
with h:
    fresh_session()
    trade.note_shop_opened()
    check("opening starts the clock", trade.shop_session_age() is not None, "")
    check("a fresh session is not expired",
          trade.shop_session_expired() is False, "")

    opened_at = trade._shop_open_since
    trade.note_shop_opened()
    check("opening again does not restart it",
          trade._shop_open_since == opened_at,
          "the clock measures how long the WINDOW has been up, so a no-op "
          "open must not extend it")

    h.clock.sleep(trade.SHOP_SESSION_SECONDS - 1)
    check("still live one second before the limit",
          trade.shop_session_expired() is False,
          f"age {trade.shop_session_age():.0f}s")
    h.clock.sleep(2)
    check("expired once past it", trade.shop_session_expired() is True,
          f"age {trade.shop_session_age():.0f}s")

    trade.note_shop_closed()
    check("closing clears it", trade.shop_session_age() is None, "")

fresh_session()


section("rows within a session do not reopen the shop")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    h.patch("open_trade_window", faithful_open(h))
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    for row in (1, 2, 3, 4):
        run(trade.relist, row, None, None, False, 8.0, True)

    kept = h.out().count("Leaving the Agent Shop open")
    check("all four rows leave the shop open", kept == 4,
          f"{kept} of 4 -- each close costs ~34s of NPC walk and tab reopen")
    check("the window is still open at the end", h.trade_open is True,
          f"trade_open={h.trade_open!r}")
    check("the session is still running", trade.shop_session_age() is not None,
          "")
    escapes = h.names().count("press_escape")
    check("nothing was escaped shut", escapes == 0,
          f"{escapes} escape(s) sent")

fresh_session()


section("past 15 minutes it closes and rebuilds from the NPC")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    h.patch("open_trade_window", faithful_open(h))
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)

    run(trade.relist, 1, None, None, False, 8.0, True)
    check("row 1 leaves it open", trade.shop_session_age() is not None, "")

    h.clock.sleep(trade.SHOP_SESSION_SECONDS + 1)
    run(trade.relist, 2, None, None, False, 8.0, True)
    check("an aged session is retired", trade.shop_session_age() is None,
          f"age {trade.shop_session_age()!r} -- a window open this long may "
          f"have been closed, moved or wedged, and only the NPC rebuild fixes "
          f"all three")
    check("...and it was actually closed", h.trade_open is False,
          f"trade_open={h.trade_open!r}")

fresh_session()


section("failure paths clear the clock (fail closed)")

h = Harness(rows=rows(), panel=empty_panel())
with h:
    fresh_session()
    trade.note_shop_opened()
    h.clock.sleep(trade.SHOP_SESSION_SECONDS + 1)
    h.patch("trade_window_open",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    h.patch("cancel_item", lambda *a, **k: True)
    h.patch("register_item", lambda *a, **k: True)
    run(trade.relist, 1, None, None, False, 8.0, False)

    check("a probe that explodes still clears the session",
          trade.shop_session_age() is None,
          "an unknown window state treated as a live session would skip the "
          "NPC rebuild for the next 15 minutes")

fresh_session()

h = Harness(rows=rows(), panel=empty_panel())
with h:
    fresh_session()
    trade.note_shop_opened()
    h.trade_open = True
    run(trade.leave_shop)
    check("leave_shop ends the session", trade.shop_session_age() is None,
          "otherwise the NEXT run inherits a session it never opened and "
          "skips the rebuild on its first row")

fresh_session()


section("a dry run still touches nothing")

h = Harness(rows=rows(), panel=empty_panel())
with h:
    fresh_session()
    run(trade.relist, 1, None, None, True, 8.0, False)
    check("dry run sends no escape", "press_escape" not in h.names(),
          f"{h.names()}")

fresh_session()




section("a failed run returns the game to its default state")


def loop_ending_in(h, outcome):
    h.patch("prepare_for_actions", lambda *a, **k: True)
    h.patch("run_sequence", outcome)
    h.trade_open = True
    return run(trade.run_loop, ["relist-rows 1-10"], 5.0, 0.0)


h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    loop_ending_in(h, lambda *a, **k: False)
    check("the breaker path closes the shop", h.trade_open is False,
          "a run stopped by the failure breaker must not leave the Trade "
          "window covering the NPC for the next run")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    def interrupt(*a, **k):
        raise KeyboardInterrupt
    loop_ending_in(h, interrupt)
    check("Ctrl+C closes the shop", h.trade_open is False, "")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    def fatal(*a, **k):
        raise trade.FatalAbort("something listed wrong")
    loop_ending_in(h, fatal)
    check("a FatalAbort closes the shop", h.trade_open is False,
          "the run stops for a human either way; it should not also leave the "
          "game wedged")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    ok, exc = loop_ending_in(h, lambda *a, **k: True)
    check("a successful run closes the shop too", h.trade_open is False, "")
    check("...and still reports success", ok is True, f"{ok!r} {exc!r}")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()
    h.patch("leave_shop",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    h.patch("prepare_for_actions", lambda *a, **k: True)
    h.patch("run_sequence", lambda *a, **k: True)
    ok, exc = run(trade.run_loop, ["relist-rows 1-10"], 5.0, 0.0)
    check("a tidy-up that explodes does not crash the run", exc is None,
          f"{exc!r}")

fresh_session()




section("a Ctrl+C during the tidy-up is announced, not swallowed")

h = Harness(rows=rows(), panel=empty_panel(), verbose=False)
with h:
    fresh_session()

    def interrupted_tidy(*a, **k):
        raise KeyboardInterrupt

    h.patch("leave_shop", interrupted_tidy)
    h.patch("prepare_for_actions", lambda *a, **k: True)
    h.patch("run_sequence", lambda *a, **k: True)
    ok, exc = run(trade.run_loop, ["relist-rows 1-10"], 5.0, 0.0)

    check("the interrupt is honoured, not swallowed",
          isinstance(exc, KeyboardInterrupt),
          f"got {exc!r} -- someone pressing Ctrl+C during shutdown wants out "
          f"now; holding them there to tidy is the wrong way round")
    check("...but the operator is told the shop may still be open",
          h.said("may still be open"), h.out()[-300:])
    check("...and told what it costs", h.said("cannot see the NPC"),
          h.out()[-300:])

fresh_session()


raise SystemExit(summary())
