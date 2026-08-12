"""The sold-out-scope check, and the run it ended six hours early.

On 2026-08-12 a `--relist-rows 1-4 --chaos` run stopped at 05:23 with 6 hours
still on the clock. Every step had behaved correctly:

  * chaos rows 1-4 sold out;
  * the spread collapsed to `Chaos Core 687,000 / Set per unit 690,000 /
    margin 3,000` against a 10,000 floor, so chaos correctly refused to rebuy;
  * the batch found its four rows empty and re-read to be sure.

The re-read called await_rows, which reads the WHOLE VISIBLE TABLE rather than
the rows the batch asked for. It saw the six live rows at 5-10, concluded "the
first read caught the table mid-refresh", and failed the cycle. Rows 5-10 were
still live next cycle, and the one after, so the breaker stopped the run.

The distinction the code was missing is a third outcome. `ShopEmpty` means the
work is finished; a failed cycle means something is broken; this is neither --
the batch's own rows sold and the thing that refills them declined to, on
purpose, and it may not decline next cycle.

These drive the REAL relist_rows through a stubbed table read. Nothing here
touches the game.
"""
import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_SCRATCH = _Path(tempfile.mkdtemp(prefix="cabal_idle_test_"))
os.environ["CABAL_SALES_DB"] = str(_SCRATCH / "scratch.db")

# NO GAME INPUT FROM A TEST. Imported before trade is used, so
# every click, keystroke, wheel turn and screen grab raises
# instead of reaching the live client. On 2026-08-12 a test
# called the real restock pipeline and drove the operator's
# game for over two minutes.
import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard  # noqa: F401  -- arms every input primitive to raise

import trade as m  # noqa: E402

CHECKS = FAILED = 0


def check(cond, what):
    global CHECKS, FAILED
    CHECKS += 1
    if not cond:
        FAILED += 1
        print(f"  FAIL  {what}")


def row(index, action, name="Chaos Core Set X 200"):
    top = 200 + index * m.REF_ROW_PITCH
    return m.Row(index=index,
                 name=name if action != "register" else "",
                 change=(1126, top + 20),
                 top=top,
                 bottom=top + m.REF_ROW_PITCH,
                 action=action,
                 price=100 if action != "register" else None,
                 qty=1 if action != "register" else 0,
                 status="On Sale" if action == "change" else "")


def drive(first, again, rows=(1, 2, 3, 4), chaos=False, holding_off=False):
    """Run relist_rows against a scripted pair of table reads.

    `chaos` / `holding_off` stand in for a chaos pass that ran this cycle and
    declined to buy on a thin spread.

    Returns ("return", value) or ("raise", ExceptionClassName).
    """
    reads = [list(first), list(again)]

    def fake_await(*a, **k):
        return reads.pop(0) if reads else list(again)

    saved = {
        "await_rows": m.await_rows,
        "read_rows": m.read_rows,
        "ensure_work_tab_empty": m.ensure_work_tab_empty,
        "ensure_shop_ready": m.ensure_shop_ready,
        "chaos_pass": m.chaos_pass,
        "restock_pass": m.restock_pass,
        "leave_shop": m.leave_shop,
        "CHAOS_ENABLED": m.CHAOS_ENABLED,
        "BUY_ENABLED": m.BUY_ENABLED,
        "CHAOS_HELD_OFF_ON_MARGIN": m.CHAOS_HELD_OFF_ON_MARGIN,
        "CHAOS_HELD_OFF_MARGIN": m.CHAOS_HELD_OFF_MARGIN,
        "CHAOS_HELD_OFF": m.CHAOS_HELD_OFF,
        # NOT grab: _no_input_guard already replaces it with a blank frame of
        # the right size. Stubbing it to return None here made every caller
        # that does img.crop(...) raise AttributeError, and drive() catches
        # exceptions to report the outcome -- so the test read that as "the
        # function under test raised" and failed seven checks for a reason
        # that had nothing to do with the code under test.
        "park_cursor": m.park_cursor,
        "leave_for_restock": getattr(m, "leave_for_restock", None),
        "note_range_view": getattr(m, "note_range_view", None),
    }
    m.park_cursor = lambda *a, **k: None
    if hasattr(m, "leave_for_restock"):
        m.leave_for_restock = lambda *a, **k: True
    if hasattr(m, "note_range_view"):
        m.note_range_view = lambda *a, **k: None
    m.CHAOS_HELD_OFF_ON_MARGIN = holding_off
    m.CHAOS_HELD_OFF_MARGIN = 3_000 if holding_off else None
    # CHAOS_HELD_OFF is the INVERTED umbrella flag: True means "the pass
    # listed nothing, for whatever reason". "not holding off" in this test
    # means chaos looked and the shelf was fine, so it must be False -- the
    # module default is True precisely so an unexamined pass cannot read as
    # sold out.
    m.CHAOS_HELD_OFF = holding_off
    m.await_rows = fake_await
    m.read_rows = fake_await
    m.ensure_work_tab_empty = lambda *a, **k: True
    m.ensure_shop_ready = lambda *a, **k: True
    m.chaos_pass = lambda *a, **k: True
    m.restock_pass = lambda *a, **k: True
    m.leave_shop = lambda *a, **k: None
    m.CHAOS_ENABLED = chaos
    m.BUY_ENABLED = False
    try:
        out = m.relist_rows(list(rows), verbose=False)
        return ("return", out)
    except Exception as exc:  # noqa: BLE001 - the outcome under test
        return ("raise", type(exc).__name__)
    finally:
        for k, v in saved.items():
            if v is not None or hasattr(m, k):
                setattr(m, k, v)


print("idle cycle / sold-out scope")

# ---------------------------------------------------------------- the bug ---
# THE 2026-08-12 RUN, EXACTLY. Rows 1-4 empty, rows 5-10 live.
empty_scope = [row(i, "register") for i in range(1, 5)]
live_outside = empty_scope + [row(i, "change") for i in range(5, 11)]

kind, what = drive(live_outside, live_outside)
check((kind, what) == ("raise", "ShopIdle"),
      f"rows 1-4 empty with 6 live rows outside the batch is ShopIdle, "
      f"got {kind} {what!r}")

# The old behaviour, stated as a literal so a revert fails here: it returned
# False, which run_loop counted as a failed cycle and fed to the breaker.
check(not (kind == "return" and what is False),
      "a sold-out scope must NOT return False -- that is what tripped "
      "MAX_CONSECUTIVE_FAILURES three cycles later")

# ------------------------------------------------------- still protected ---
# The case the re-read was built for MUST still fail the cycle: our own rows
# read empty once and are live on a fresh read, so the first frame was bad.
kind, what = drive(empty_scope,
                   [row(i, "change") for i in range(1, 5)])
check((kind, what) == ("return", False),
      f"a genuine mid-refresh still fails the cycle so it retries, "
      f"got {kind} {what!r}")

# ------------------------------------------------------------- sold out ---
# Nothing live anywhere: the shop really has sold out and the run is finished.
kind, what = drive([row(i, "register") for i in range(1, 11)],
                   [row(i, "register") for i in range(1, 11)])
check((kind, what) == ("raise", "ShopEmpty"),
      f"an empty table everywhere is still ShopEmpty, got {kind} {what!r}")

# ------------------------------------------------------------ scoping ----
# A batch asking for rows 5-10 must judge on rows 5-10, not on rows 1-4.
kind, what = drive(live_outside, live_outside, rows=(5, 6, 7, 8, 9, 10))
check(kind == "return",
      f"a batch scoped to live rows proceeds normally, got {kind} {what!r}")

# ------------------------------------- an empty shop that is only WAITING ---
# The operator's requirement, 2026-08-12: "it can be empty for hours then once
# margin is back up we start running". A chaos-only shop whose every row sold
# and whose spread then collapsed must NOT end the run.
all_empty = [row(i, "register") for i in range(1, 11)]

kind, what = drive(all_empty, all_empty, chaos=True, holding_off=True)
check((kind, what) == ("raise", "ShopIdle"),
      f"an empty shop with chaos holding off on margin is ShopIdle, not the "
      f"end of the run, got {kind} {what!r}")
check(what != "ShopEmpty",
      "ShopEmpty here would close the shop and stop the run at exactly the "
      "moment the strategy is meant to be sitting still")

# ...but a genuinely finished shop still finishes. Chaos on, NOT holding off:
# it had every chance to refill and did not, so there is nothing to wait for.
kind, what = drive(all_empty, all_empty, chaos=True, holding_off=False)
check((kind, what) == ("raise", "ShopEmpty"),
      f"an empty shop with chaos NOT holding off is still ShopEmpty, "
      f"got {kind} {what!r}")

# THE UMBRELLA FLAG DEFAULTS TO HELD. A chaos pass that returned early for any
# of its nine other reasons -- shelf unreadable, margin unread, Purchase tab
# unreachable, buying halted -- left both specific flags False and the empty
# batch then raised ShopEmpty, ending the run under a SOLD OUT headline with
# the market intact.
check(m.CHAOS_HELD_OFF is True,
      "CHAOS_HELD_OFF must default to True so an unexamined pass cannot read "
      "as a sold-out shop")

# And with chaos off entirely, the flag must not resurrect a finished run.
kind, what = drive(all_empty, all_empty, chaos=False, holding_off=True)
check((kind, what) == ("raise", "ShopEmpty"),
      f"a stale hold-off flag with --chaos off must not keep a sold-out run "
      f"alive, got {kind} {what!r}")

# --------------------------------------------------- the flag's lifetime ---
# It must describe THIS cycle. A pass that resets it at the top is what stops
# one thin-margin cycle from making every later empty shop look like a wait.
src_pass = (_ROOT / "trade.py").read_text(encoding="utf-8-sig")
body = src_pass[src_pass.index("def chaos_pass"):]
body = body[:body.index("        if margin <= CHAOS_MARGIN_FLOOR")]
check("CHAOS_HELD_OFF_ON_MARGIN = False" in body,
      "chaos_pass must clear the hold-off flag at the top of every pass")

# --------------------------------------------------- the loop's counters ---
# ShopIdle must reach run_loop as neither a success nor a failure.
src = (_ROOT / "trade.py").read_text(encoding="utf-8-sig")
handler = src[src.index("except ShopIdle"):src.index("except FatalAbort")]
check("succeeded += 1" not in handler,
      "an idle cycle must not count as a success -- that is how a run reports "
      "green cycles having relisted nothing")
check("failures += 1" not in handler and "consecutive += 1" not in handler,
      "an idle cycle must not count as a failure -- that is what ended the "
      "2026-08-12 run early")
check("consecutive = 0" not in handler,
      "an idle cycle must not RESET the breaker either: a real fault followed "
      "by a quiet market must still reach it")

print(f"idle cycle: {CHECKS} checks, {FAILED} failed")


# ======================================================================
# THE RESTOCK VS THE CHAOS SHELF
# ======================================================================
# This was a shortfall reservation: chaos published what it was short of and
# the restock left that many rows free. Replaced on the operator's instruction
# with a flat cap -- "normal cores can only use up to 12 rows total, doesn't
# matter where 12 rows, chaos will have space for 4 rows no matter what."
#
# The cap is stronger. A shortfall reservation is only as good as the moment it
# is taken, and the moment chaos is idling on a thin spread is exactly when it
# is short of nothing it can act on.
#
# Those checks now live in chaos_priority_test.py, driven through the real
# restock_core room gate rather than read off the source.

print(f"idle cycle + priority: {CHECKS} checks, {FAILED} failed")
sys.exit(1 if FAILED else 0)
