"""A Core that sells out mid-batch is resupplied now, not next cycle.

Sold-out detection used to happen once, in restock_pass, before the row loop.
A Core that sold out at row 3 of a fifteen-row batch therefore sat unstocked
for the rest of that cycle -- ten to fifteen minutes with none of an item on
the shelf that was selling fast enough to clear out.

The batch now stops as soon as the last row of an enabled Core is seen to be
gone, and returns SUCCESS so run_loop starts the next cycle immediately. That
cycle's first act is restock_pass, so the resupply happens within seconds and
the rows that were not reached are relisted straight after, from a fresh read.

WHAT THIS FILE IS CAREFUL ABOUT, because both are ways to make it useless:

  * It must return TRUE. Returning False would count against
    MAX_CONSECUTIVE_FAILURES and stop the whole run for doing exactly what it
    was asked to do -- three sell-outs and the run is over.
  * It must NOT fire on a Core that is merely off-screen. core_row_counts over
    the ten visible rows says 0 for anything further down the shop, which is
    why restock_pass sweeps before believing it. Firing on that would cut
    every batch short at the first row.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
# restock_is_armed() requires BUY_ENABLED, which main() sets from --buy. Without
# it the mid-cycle interrupt can never fire and every case below would pass for
# the wrong reason.
m.BUY_ENABLED = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def row(index, name, action="change", qty=None, price=None):
    return m.Row(index=index, name=name, change=(1126, 545), top=0, bottom=0,
                 action=action, price=price, qty=qty)


CORE = "Force Core(High)"           # slot 7, enabled in ENABLE_BUYING
OTHER = "Epic Booster (Highest)"


class Batch:
    """Drives relist_rows with the game replaced.

    `vanish` names rows whose listing has gone from the shop between the
    catalogue being read and the row being reached -- i.e. it sold.
    """

    def __init__(self, shop, vanish=()):
        self.shop = shop
        self.vanish = set(vanish)
        self.relisted = []
        self.restocked = []
        self.events = []          # record() labels, so the interrupt is visible
        self.saved = {}

    def __enter__(self):
        names = ("ensure_shop_ready", "ensure_work_tab_empty", "restock_pass",
                 "await_rows", "enumerate_listings", "bring_into_view",
                 "relist", "record", "shop_rows_used", "leave_shop",
                 "require_empty_work_tab", "avoid_warlag")
        for n in names:
            self.saved[n] = getattr(m, n)
        m.ensure_shop_ready = lambda verbose=True: True
        m.ensure_work_tab_empty = lambda timeout=8.0, verbose=True: True
        # `scope` is the rows this batch was asked for. restock_pass takes
        # it so the sold-out decision is confined to them: "if i relist
        # 1-4 ... if the item doesn't exist there, go resupply those,
        # regardless of what's in bottom rows".
        m.restock_pass = (lambda timeout=8.0, verbose=True, scope=None:
                          self.restocked.append(scope or 1))
        m.record = lambda label, *a, **k: self.events.append(label)
        m.shop_rows_used = lambda verbose=True: len(self.shop)
        m.leave_shop = lambda verbose=True: True
        m.require_empty_work_tab = lambda verbose=True: True
        m.avoid_warlag = lambda allowance=0.0, verbose=True, dry_run=False: 0.0
        m.await_rows = lambda timeout=8.0, poll=0.5: self._visible()
        m.enumerate_listings = lambda timeout=8.0, verbose=True: [
            (r.index, r) for r in self.shop]
        def _biv(ref, timeout=8.0, verbose=True, hint=None, report=None):
            self._hint = hint or 1
            return self._view(ref, report)

        m.bring_into_view = _biv
        m.relist = self._relist
        return self

    def __exit__(self, *exc):
        for n, v in self.saved.items():
            setattr(m, n, v)

    def _live(self):
        """The shop as it stands now, RENUMBERED -- which is what the game
        does when a listing sells: everything below it moves up one."""
        out = []
        for r in self.shop:
            if r.index in self.vanish:
                continue
            out.append(row(len(out) + 1, r.name, r.action, r.qty, r.price))
        return out

    def _visible(self):
        return self._live()[:10]

    def _view(self, ref, report):
        """A ten-row window positioned so `hint` falls inside it."""
        live = self._live()
        hint = getattr(self, "_hint", 1)
        top = max(1, min(hint, max(1, len(live) - 9)))
        if report is not None:
            report["top_index"] = top
        return live[top - 1:top - 1 + 10]

    def _relist(self, row_index, inv_row=None, inv_col=None, dry_run=False,
                timeout=8.0, verbose=True, attempts=3, expect=None):
        self.relisted.append(row_index)
        return m.RELISTED


# -- it fires when the last row of an enabled Core disappears --------------
# Two Force Core(High) rows; both have sold by the time the batch reaches them.
# The Core sits in the MIDDLE. It has to: the interrupt is guarded on
# `position < len(targets)`, because stopping on the very last row skips
# nothing and gains nothing -- the batch was about to end anyway. A scenario
# with the Core last therefore tests the guard, not the feature.
SHOP = [row(1, OTHER, qty=8, price=54_000_000),
        row(2, OTHER, qty=8, price=54_000_000),
        row(3, CORE, qty=250, price=222_067),
        row(4, CORE, qty=250, price=222_067),
        row(5, OTHER, qty=8, price=54_000_000),
        row(6, OTHER, qty=8, price=54_000_000)]

with Batch(SHOP, vanish={3, 4}) as b:
    ok = m.relist_rows([1, 2, 3, 4, 5, 6], verbose=False)

check(ok is True,
      f"the batch must report SUCCESS -- returning False counts against "
      f"MAX_CONSECUTIVE_FAILURES and three sell-outs would end the run. "
      f"got {ok!r}")
check(b.relisted == [1, 2],
      f"the rows before the sell-out are still done, got {b.relisted}")
check("relist.mid_cycle_restock" in b.events,
      f"the sell-out must be RECORDED as a mid-cycle restock -- without this "
      f"assertion the test cannot tell an interrupt from a row that was "
      f"merely skipped, and passes with the feature switched off. events="
      f"{b.events}")

# Only the LAST row going matters: one of two is not a sell-out, so the
# mid-cycle interrupt must not fire.
#
# The batch may still STOP -- when one of two identical stacks sells, every row
# below it renumbers and there is no way to tell which twin is which, so the
# sibling guard refuses rather than cancelling a listing nobody named. What
# matters here is that it is not reported as a sell-out.
with Batch(SHOP, vanish={3}) as b:
    m.relist_rows([1, 2, 3, 4, 5, 6], verbose=False)
check(b.relisted and b.relisted[0] == 1,
      f"it gets started at least, got {b.relisted}")


# -- it must NOT fire for a Core that is merely further down the shop ------
# The check is driven off the whole-shop catalogue for exactly this reason:
# core_row_counts over the visible ten says 0 for anything below them.
DEEP = ([row(i, OTHER, qty=8, price=54_000_000) for i in range(1, 11)]
        + [row(11, CORE, qty=250, price=222_067),
           row(12, CORE, qty=250, price=222_067)])
with Batch(DEEP) as b:
    ok = m.relist_rows(list(range(1, 13)), verbose=False)
check(ok is True, f"an off-screen Core does not stop the batch, got {ok!r}")
check("relist.mid_cycle_restock" not in b.events,
      f"and no sell-out is recorded for a Core that is merely further down, "
      f"got {b.events}")
check(len(b.relisted) == 12,
      f"every row is still relisted -- the Core is off-screen, not sold out. "
      f"got {len(b.relisted)} row(s): {b.relisted}")


# -- a disabled Core must not trigger it ----------------------------------
_saved_enable = dict(m.ENABLE_BUYING)
try:
    m.ENABLE_BUYING[CORE] = False
    with Batch(SHOP, vanish={3, 4}) as b:
        ok = m.relist_rows([1, 2, 3, 4, 5, 6], verbose=False)
    # The batch may still stop -- vanished rows renumber the shop and the
    # sibling guard refuses rather than guessing which twin is which. What
    # must NOT happen is the sell-out being treated as one.
    check("relist.mid_cycle_restock" not in b.events,
          f"a disabled Core selling out is not a mid-cycle restock; nothing "
          f"should be recorded, got {b.events}")
    check(len(b.relisted) >= 2,
          f"and the rows before it are still done, got {b.relisted}")
finally:
    m.ENABLE_BUYING.clear()
    m.ENABLE_BUYING.update(_saved_enable)

# -- and the switch turns it off ------------------------------------------
_saved_flag = m.RESTOCK_MID_CYCLE
try:
    m.RESTOCK_MID_CYCLE = False
    with Batch(SHOP, vanish={3, 4}) as b:
        ok = m.relist_rows([1, 2, 3, 4, 5, 6], verbose=False)
    check("relist.mid_cycle_restock" not in b.events,
          f"with RESTOCK_MID_CYCLE off nothing is recorded, got {b.events}")
finally:
    m.RESTOCK_MID_CYCLE = _saved_flag

check(m.RESTOCK_MID_CYCLE is _saved_flag, "the flag was restored")


print(f"mid_cycle_restock_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
