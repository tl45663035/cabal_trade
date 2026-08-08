"""A stranded work tab STOPS THE RUN. It is no longer cleared automatically.

This file used to assert the opposite, and the reversal is worth recording
rather than quietly rewriting.

WHAT THE OLD DESIGN WAS. A cancel commits and its re-list does not, so the
stack sits in inventory tab 4. Every later cycle refuses to start -- correctly,
because with items already in the tab the before/after diff cannot tell which
slots a NEW cancel filled -- so the run died three cycles later having done
nothing. recover_stranded_work_tab was written to clear it: an item in an
inventory slot cannot be NAMED (there is no text to read), so its floor cannot
be looked up, so the recovery listed it at strictest_price_floor() -- above
every floor by construction -- and let the next cycle read the name off the
TABLE and re-price it properly. One overpriced cycle to buy back a dead run.

WHY IT WAS ABANDONED. strictest_price_floor() is 175,000,000. On 2026-08-08 the
recovery reached for that price twice against 54 Upgrade Core (Ultimate) worth
469,469 each, and was stopped only by the client being disconnected at the
time. It is the one path in the file that commits real money to a decision
nobody made, and it fires precisely when the script is already confused about
what is where. The "temporary" overprice also assumes a NEXT cycle that
re-prices -- which is exactly what a strand tends to prevent.

The operator's rule, 2026-08-08: always terminate if tab 4 is not empty. A
human clears it in a minute. A wrong 175,000,000 listing costs a row, a
registration fee on an inflated figure, and a position nobody chose.

So what is tested here now is that the refusal is total: fatal rather than
per-cycle, taken before anything is clicked, and not reachable around.
"""
import inspect

from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def live_rows(n=4):
    return [make_row(i, f"Item {i:02d}", price=100_000 + i, qty=50 + i)
            for i in range(1, n + 1)]


class Tab(Harness):
    """A work tab that is dirty or clean, as asked."""

    def __init__(self, occupied, **kw):
        super().__init__(rows=live_rows(), panel=empty_panel(), **kw)
        self._occupied = occupied

    def _occupied_slots(self, image=None, origin=None):
        return list(self._occupied)

    def _require_empty_work_tab(self, verbose=True):
        return not self._occupied


# ===========================================================================
section("a dirty work tab is FATAL, not a failed cycle")

h = Tab(occupied=[(1, 1), (1, 2), (1, 3)])
with h:
    ok, exc = run(trade.ensure_work_tab_empty)

check("a dirty tab raises rather than returning",
      exc is not None, f"returned {ok!r}")
check("and the exception is FatalAbort, so the RUN stops",
      isinstance(exc, trade.FatalAbort),
      f"got {type(exc).__name__ if exc else None}: a per-cycle failure would "
      f"be retried, and a strand does not clear itself -- retrying only spends "
      f"the breaker's budget arriving at the same place")
check("the message names the tab",
      exc is not None and f"tab {trade.WORK_TAB}" in str(exc), str(exc))
check("and says nothing was changed",
      exc is not None and "Nothing has been listed or cancelled" in str(exc),
      str(exc))
check("nothing was clicked", not h.clicks(), str(h.clicks()))


# ===========================================================================
section("the 175,000,000 path is not reachable from the batch")

src = inspect.getsource(trade.ensure_work_tab_empty)
check("ensure_work_tab_empty does not call the recovery",
      "recover_stranded_work_tab" not in src.split('"""')[-1],
      "the whole point of the change is that the automatic path is gone")
check("it raises FatalAbort", "FatalAbort" in src, src[-300:])

# The recovery itself is KEPT, deliberately. It is the only code that knows how
# to clear a strand, and a future version could call it with a NAME to price
# against -- which is the missing piece that made it dangerous. Kept unwired,
# not kept running.
check("recover_stranded_work_tab still exists",
      callable(getattr(trade, "recover_stranded_work_tab", None)),
      "deleting it would lose the only code that knows how to clear a strand")

callers = [name for name in dir(trade)
           if callable(getattr(trade, name, None))
           and not name.startswith("__")
           and name not in ("recover_stranded_work_tab",)
           and "recover_stranded_work_tab" in (
               inspect.getsource(getattr(trade, name))
               if getattr(getattr(trade, name), "__module__", "") == "trade"
               and inspect.isfunction(getattr(trade, name)) else "")]
check("and nothing calls it automatically any more",
      callers == [],
      f"still called by: {callers} -- an automatic caller puts the "
      f"175,000,000 listing back on the table")


# ===========================================================================
section("a clean tab still costs nothing and passes")

h = Tab(occupied=[])
with h:
    ok, exc = run(trade.ensure_work_tab_empty)
check("a clean tab returns True", ok is True, f"got {ok!r} / {exc!r}")
check("with no clicks", not h.clicks(), str(h.clicks()))


# ===========================================================================
section("mid-batch, a dirty tab was always a failure to report")

# Unchanged by any of this, and worth stating: the START-of-batch check and the
# MID-batch check mean different things. Mid-batch a dirty tab means the row
# just relisted stranded something -- a real failure about THIS row -- and it
# has always used require_empty_work_tab directly.
relist_src = inspect.getsource(trade.relist_rows)
check("relist_rows still calls require_empty_work_tab mid-batch",
      "require_empty_work_tab" in relist_src, "")
# Twice since 2026-08-08: once before the resupply and once after it. The
# resupply buys, converts and lists on the work tab, so a batch that started
# clean can be dirty by the time the relisting begins -- and starting the
# relist on a dirty tab is the state this whole check exists to prevent.
check("and ensure_work_tab_empty guards BOTH sides of the resupply",
      relist_src.count("ensure_work_tab_empty(") == 2,
      f"found {relist_src.count('ensure_work_tab_empty(')} -- the resupply "
      f"works on that tab, so checking only before it would miss a strand it "
      f"created")


raise SystemExit(summary())
