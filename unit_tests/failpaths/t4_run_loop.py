"""run_loop() failure paths: which failures stop the run and which retry."""
import harness as H
from harness import (Harness, check, note, section, summary, run, make_row,
                     empty_panel)
import trade

ITEM = "Upgrade Core (Ultimate)"


def fresh(**flags):
    h = Harness(rows=[make_row(1, ITEM, price=410_000, qty=100)],
                panel=empty_panel())
    for key, value in flags.items():
        setattr(h, key, value)
    return h


def loop_with(sequence, minutes=0.2, actions=("relist-rows 1",)):
    """Drive run_loop with run_sequence replaced by `sequence`."""
    h = fresh()
    with h:
        h.patch("run_sequence", sequence)
        ok, exc = run(trade.run_loop, list(actions), minutes, 0.0)
    return h, ok, exc


def cycles(h) -> int:
    return h.out().count("===== cycle ")


# ---------------------------------------------------------------------------
section("4a. a cycle raising PermissionError")
# SHOULD stop: retrying cannot fix blocked input.
def refused(*a, **k):
    raise PermissionError(trade.CURSOR_BLOCKED_HINT)


h, ok, exc = loop_with(refused)
check("4a the loop stops after one cycle", cycles(h) == 1, f"{cycles(h)}")
check("4a returns False", ok is False, f"got {ok!r}")
check("4a said input was refused", h.said("Input was refused"), h.out()[-400:])
check("4a said it is terminating", h.said("Terminating the loop"), h.out()[-400:])
check("4a raised nothing at the top level", exc is None, repr(exc))
check("4a the stop IS recorded", "loop.stopped" in h.labels(), str(h.labels()))
check("4a with the reason", (h.rec("loop.stopped") or {}).get("reason")
      == "permission", str(h.rec("loop.stopped")))
note("4a", "cycle.start/loop.stopped are recent additions (their own comments "
     "date them to the 19:56 outage). The build that produced the recorded "
     "index has none of them: run_index.jsonl contains 0 cycle.start, "
     "0 cycle.end and 0 loop.stopped entries in 3,776 lines.")


# ---------------------------------------------------------------------------
section("4b. a cycle raising a generic Exception")
state = {"n": 0}


def flaky(*a, **k):
    state["n"] += 1
    if state["n"] == 1:
        raise RuntimeError("tesseract not found")
    return True


h, ok, exc = loop_with(flaky)
check("4b a generic exception does NOT stop the run", cycles(h) > 1,
      f"{cycles(h)} cycle(s)")
check("4b it was reported", h.said("raised RuntimeError"), h.out()[:600])
check("4b it said it would retry", h.said("Will retry next cycle"),
      h.out()[:800])
check("4b later cycles succeeded", ok is True, f"got {ok!r}")


# ---------------------------------------------------------------------------
section("4c. three consecutive failures")
def always_raises(*a, **k):
    raise RuntimeError("a coordinate is wrong")


h, ok, exc = loop_with(always_raises, minutes=1.0)
check(f"4c stops at MAX_CONSECUTIVE_FAILURES={trade.MAX_CONSECUTIVE_FAILURES}",
      cycles(h) == trade.MAX_CONSECUTIVE_FAILURES, f"{cycles(h)} cycles")
check("4c said why", h.said("cycles have failed in a row"), h.out()[-500:])
check("4c returns False", ok is False, f"got {ok!r}")

# the same, but returning False rather than raising
def always_false(*a, **k):
    return False


h, ok, exc = loop_with(always_false, minutes=1.0)
check("4c2 three False cycles also stop the run",
      cycles(h) == trade.MAX_CONSECUTIVE_FAILURES, f"{cycles(h)} cycles")


# ---------------------------------------------------------------------------
section("4d. FatalAbort")
def fatal(*a, **k):
    raise trade.FatalAbort("listed the wrong thing; withdrawn")


h, ok, exc = loop_with(fatal, minutes=1.0)
check("4d stops immediately", cycles(h) == 1, f"{cycles(h)}")
check("4d said FATAL", h.said("FATAL:"), h.out()[-400:])
check("4d returns False", ok is False, f"got {ok!r}")


# ---------------------------------------------------------------------------
section("4e. the workstation locks")
h = fresh(locked=True)
with h:
    h.patch("run_sequence", lambda *a, **k: True)
    ok, exc = run(trade.run_loop, ["relist-rows 1"], 1.0, 0.0)
check("4e stops on the first cycle", cycles(h) == 1, f"{cycles(h)}")
check("4e said the workstation is locked", h.said("workstation is locked"),
      h.out()[-400:])


# ---------------------------------------------------------------------------
section("4f. a cycle that did nothing is not a success")
# FIXED 2026-08-08. run_sequence returned True for an empty action list and
# run_loop counted it as a succeeded cycle, so a loop with nothing to do stayed
# green for hours AND reset the consecutive-failure breaker -- the breaker that
# exists to stop exactly that.
#
# The two checks that used to assert the bug ("produces green cycles",
# "reports overall success") are gone rather than inverted: they described the
# defect, and a defect that no longer exists should not leave a test asserting
# its symptoms.
h = fresh()
with h:
    ok, exc = run(trade.run_loop, [], 0.05, 0.0)
check("4f a cycle that did no work must not count as a success", ok is not True,
      f"got {ok!r} -- an 8-hour run of empty cycles used to report 'succeeded'")
check("4f and it says why rather than failing silently",
      h.said("No actions to run") or h.said("caller error"),
      h.out()[-400:])

# (ii) a table of empty rows. DIFFERENT, and still True on purpose: the shop
# genuinely having nothing to relist is a legitimate outcome, not a caller
# error. Kept beside 4f because the two look alike and are not.
h = fresh(rows=[make_row(1, "(empty)", action="register", price=None, qty=None),
                make_row(2, "(empty)", action="register", price=None, qty=None)])
with h:
    ok2, exc = run(trade.relist_rows, [1, 2])
# An all-empty shop raises ShopEmpty, and that IS the success signal: main()
# catches it, prints "SOLD OUT" and exits 0, precisely so that scripting this
# does not read "finished" as "failed". The old assertion here wanted True and
# had been failing since before 2026-08-08 -- a stale expectation, not a defect.
check("4f2 an all-empty shop signals SOLD OUT rather than failing",
      isinstance(exc, trade.ShopEmpty),
      f"got {ok2!r} / {exc!r}")
check("4f2 and ShopEmpty is deliberately not an Aborted",
      not isinstance(exc, trade.Aborted),
      "Aborted means 'this cycle did not work'; selling out is the best "
      "possible outcome and must not trip the failure breaker")


# ---------------------------------------------------------------------------
section("4g. run_sequence: Aborted stops the batch, PermissionError escapes")
h = fresh()
with h:
    h.patch("cancel_item", lambda *a, **k: (_ for _ in ()).throw(
        trade.Aborted("no dialog")))
    ok, exc = run(trade.run_sequence, ["cancel 1"])
check("4g Aborted is caught by run_sequence", ok is False and exc is None,
      f"{ok!r} {exc!r}")
check("4g and reported", h.said("Stopped:"), h.out()[-300:])

h = fresh()
with h:
    h.patch("cancel_item", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError("blocked")))
    ok, exc = run(trade.run_sequence, ["cancel 1"])
check("4g2 PermissionError escapes run_sequence deliberately",
      isinstance(exc, PermissionError), f"{ok!r} {exc!r}")


# ---------------------------------------------------------------------------
section("4h. an exception raised in the FINALLY of relist cannot mask the run")
h = fresh()
with h:
    def boom(*a, **k):
        raise RuntimeError("escape failed")
    h.patch("press_escape", boom)
    h.trade_open = True
    outcome, exc = run(trade.relist, 1, None, None, False, 8.0, True)
check("4h close_shop swallows its own failure", exc is None, repr(exc))
check("4h and notes it", h.said("could not close the Trade window"),
      h.out()[-300:])

raise SystemExit(summary())
