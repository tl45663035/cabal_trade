import inspect

from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade


def live_rows(n=4):
    return [make_row(i, f"Item {i:02d}", price=100_000 + i, qty=50 + i)
            for i in range(1, n + 1)]


class Tab(Harness):

    def __init__(self, occupied, **kw):
        super().__init__(rows=live_rows(), panel=empty_panel(), **kw)
        self._occupied = occupied

    def _occupied_slots(self, image=None, origin=None):
        return list(self._occupied)

    def _require_empty_work_tab(self, verbose=True):
        return not self._occupied


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


section("the 175,000,000 path is not reachable from the batch")

src = inspect.getsource(trade.ensure_work_tab_empty)
check("ensure_work_tab_empty does not call the recovery",
      "recover_stranded_work_tab" not in src.split('"""')[-1],
      "the whole point of the change is that the automatic path is gone")
check("it raises FatalAbort", "FatalAbort" in src, src[-300:])

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


section("a clean tab still costs nothing and passes")

h = Tab(occupied=[])
with h:
    ok, exc = run(trade.ensure_work_tab_empty)
check("a clean tab returns True", ok is True, f"got {ok!r} / {exc!r}")
check("with no clicks", not h.clicks(), str(h.clicks()))


section("mid-batch, a dirty tab was always a failure to report")

relist_src = inspect.getsource(trade.relist_rows)
check("relist_rows still calls require_empty_work_tab mid-batch",
      "require_empty_work_tab" in relist_src, "")
_guards = relist_src.count("ensure_work_tab_empty(")
check("and ensure_work_tab_empty guards every stage that touches the tab",
      _guards >= 2,
      f"found {_guards} -- the resupply and the chaos pass both work on that "
      f"tab, so checking only before them would miss a strand they created")


raise SystemExit(summary())
