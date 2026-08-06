"""Refuse to start without room for a cancelled stack to come back.

Cancelling a listing does not return it as one item: a 250-item stack comes
back as roughly 64 separate inventory slots, and the game refuses the WHOLE
cancellation rather than partially withdrawing when it will not fit.

That refusal ended two runs on 2026-08-05. Every cycle, the same row:

    ABORTED: the dialog stayed open after Confirmation.
    A likely cause is not enough free inventory space to receive the stack.
    Retrying this row will refuse identically until space is freed.

The script diagnosed it correctly and then retried it for eleven minutes,
because nothing it does can free a slot. Checking once at startup replaces
that with one line before anything is touched.

The premium tabs are the subtle part: they are padlocked, selecting one does
nothing, and counting their 128 slots as free would promise room that a
cancelled stack cannot go into -- which is exactly the promise this check
exists to stop making.
"""
from harness import Harness, check, empty_panel, make_row, run, section, summary

import trade

CAP = trade.GRID_SIZE * trade.GRID_SIZE


class Inventory(Harness):
    """An inventory with a chosen number of used slots per tab."""

    def __init__(self, used_per_tab, *a, **kw):
        super().__init__(*a, **kw)
        self.used_per_tab = dict(used_per_tab)
        self.visited: list[int] = []
        self.inventory_tab = 1

    def _select_inventory_tab(self, tab, origin=None, timeout=5.0):
        self.log("select_inventory_tab", tab)
        self.visited.append(tab)
        self.inventory_tab = tab
        return True

    def _occupied_slots(self, image=None, origin=None):
        used = self.used_per_tab.get(self.inventory_tab, 0)
        return [(1 + i // trade.GRID_SIZE, 1 + i % trade.GRID_SIZE)
                for i in range(used)]

    def install(self):
        out = super().install()
        self.patch("occupied_slots", self._occupied_slots)
        return out


def rows():
    return [make_row(1, "Item 01", price=100_000, qty=50)]


# ===========================================================================
section("what counts as usable, and what does not")

tabs = trade.usable_inventory_tabs()
check(f"usable tabs are 1..{trade.TAB_COUNT - trade.PREMIUM_TAB_COUNT}",
      tabs == list(range(1, trade.TAB_COUNT - trade.PREMIUM_TAB_COUNT + 1)),
      f"{tabs}")
check("the premium tabs are excluded",
      trade.TAB_COUNT not in tabs and trade.TAB_COUNT - 1 not in tabs,
      f"{tabs} -- counting padlocked tabs promises {trade.PREMIUM_TAB_COUNT * CAP} "
      f"slots a cancelled stack cannot use")
check("something is left to count", len(tabs) >= 1, f"{tabs}")


# ===========================================================================
section("counting free space across the usable tabs")

h = Inventory({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    got, exc = run(trade.count_inventory_space, verbose=False)
    check("all empty: counts every usable slot", exc is None and got
          and got[0] == len(tabs) * CAP,
          f"{got!r} {exc!r}")
    check("all empty: total matches the usable tabs",
          got and got[1] == len(tabs) * CAP, f"{got!r}")
    check("visited every usable tab",
          sorted(set(h.visited)) [:len(tabs)] == tabs,
          f"visited {h.visited}")
    check("never selected a premium tab",
          all(t in tabs for t in h.visited),
          f"visited {h.visited} -- a padlocked tab cannot be selected, so "
          f"trying is a wasted click at best")

h = Inventory({t: CAP for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    got, exc = run(trade.count_inventory_space, verbose=False)
    check("all full: reports zero free", got and got[0] == 0, f"{got!r}")

half = {t: (CAP if i % 2 else 0) for i, t in enumerate(tabs)}
h = Inventory(half, rows=rows(), panel=empty_panel())
with h:
    got, exc = run(trade.count_inventory_space, verbose=False)
    want = sum(CAP - used for used in half.values())
    check("mixed: free is the sum across tabs", got and got[0] == want,
          f"{got!r}, expected {want} free")


# ===========================================================================
section("the tab that was selected is put back")

h = Inventory({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    h.inventory_tab = trade.WORK_TAB
    run(trade.count_inventory_space, verbose=False)
    check("ends on the tab it started on",
          h.inventory_tab == trade.WORK_TAB,
          f"left on tab {h.inventory_tab} -- this runs at startup, and moving "
          f"the player's inventory is a rude way to answer a question")


# ===========================================================================
section("the gate: enough, not enough, and unreadable")

enough = {t: 0 for t in tabs}
h = Inventory(enough, rows=rows(), panel=empty_panel())
with h:
    ok, exc = run(trade.require_inventory_space)
    check("plenty free: allowed to start", ok is True, f"{ok!r} {exc!r}")
    check("says how much is free", h.said("slot(s) free"), h.out()[:400])

# One slot short of the minimum.
short = len(tabs) * CAP - (trade.MIN_FREE_INVENTORY - 1)
tight = {tabs[0]: min(CAP, short)}
left = short - tight[tabs[0]]
for t in tabs[1:]:
    tight[t] = min(CAP, left)
    left -= tight[t]
h = Inventory(tight, rows=rows(), panel=empty_panel())
with h:
    ok, exc = run(trade.require_inventory_space)
    check("one slot short: refused", ok is False, f"{ok!r}")
    check("says how many are needed",
          h.said(f"{trade.MIN_FREE_INVENTORY} are needed"), h.out()[-600:])
    check("explains that retrying cannot help",
          h.said("cannot be retried away") or h.said("frees a slot"),
          h.out()[-600:])

# Exactly the minimum is enough -- the check is "at least", not "more than".
exact = dict(tight)
first = tabs[0]
exact[first] = max(0, exact[first] - 1)
h = Inventory(exact, rows=rows(), panel=empty_panel())
with h:
    ok, exc = run(trade.require_inventory_space)
    check("exactly the minimum: allowed", ok is True,
          f"{ok!r} -- off-by-one here refuses a run that would have worked")


# ===========================================================================
section("unreadable inventory fails CLOSED")

class NoPanel(Inventory):
    def _inventory_origin(self, source=None, retries=3):
        return None


h = NoPanel({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    got, exc = run(trade.count_inventory_space, verbose=False)
    check("no panel: counting returns None", got is None, f"{got!r}")
    ok, exc = run(trade.require_inventory_space)
    check("no panel: the gate REFUSES", ok is False,
          f"{ok!r} -- failing open costs an hour of identical refusals; "
          f"failing closed costs one message")
    check("no panel: says why", h.said("unknown") or h.said("not visible"),
          h.out()[-300:])


class StuckTab(Inventory):
    def _select_inventory_tab(self, tab, origin=None, timeout=5.0):
        self.log("select_inventory_tab", tab)
        return False


h = StuckTab({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    got, exc = run(trade.count_inventory_space, verbose=False)
    check("tab switch fails: counting returns None", got is None, f"{got!r}")
    ok, exc = run(trade.require_inventory_space)
    check("tab switch fails: the gate REFUSES", ok is False, f"{ok!r}")


# ===========================================================================
section("the check runs where the inventory is REACHABLE")

# It first ran in main(), straight after calibration -- before the game had
# been prepared. The Inventory panel was not open, select_inventory_tab had
# nothing to click, and every run was refused for "free space unknown" on a
# game that was merely not ready. Failing closed was right; the position was
# not.
#
# It now runs inside relist_rows, after ensure_shop_ready, which is where
# require_empty_work_tab has always worked from.
h = Inventory({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    trade._SPACE_CHECKED = False
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    ok, exc = run(trade.relist_rows, [1])
    check("the batch reached the space check and passed it",
          ok is True and h.said("slot(s) free"),
          f"{ok!r} {exc!r} / {h.out()[:400]}")
    check("the shop was opened BEFORE the inventory was read",
          h.names().index("open_trade_window")
          < h.names().index("select_inventory_tab"),
          f"{h.names()[:8]} -- reading the inventory before the game is ready "
          f"is what refused every run")

# Once per process, not once per cycle: it costs a tab click and a screenshot
# per tab, and space only shrinks through a cancel the batch is about to make.
h = Inventory({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    trade._SPACE_CHECKED = False
    h.patch("relist", lambda *a, **k: trade.RELISTED)
    run(trade.relist_rows, [1])
    first = h.names().count("select_inventory_tab")
    run(trade.relist_rows, [1])
    second = h.names().count("select_inventory_tab") - first
    check("the second batch does not re-count every tab",
          second < first,
          f"{first} tab selections then {second} -- re-counting every cycle "
          f"is {len(tabs)} clicks and screenshots for a number that only "
          f"changes when this batch itself cancels something")

# A shop that will not open must not be reported as short of space.
h = Inventory({t: 0 for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    trade._SPACE_CHECKED = False
    h.patch("open_trade_window", lambda *a, **k: False)
    ok, exc = run(trade.relist_rows, [1])
    check("shop will not open: refused before the space check",
          ok is False and not h.said("slot(s) free"),
          f"{ok!r} / {h.out()[-300:]}")

# --no-space-check skips it entirely.
h = Inventory({t: CAP for t in tabs}, rows=rows(), panel=empty_panel())
with h:
    trade._SPACE_CHECKED = False
    saved = trade.SPACE_CHECK_ENABLED
    try:
        trade.SPACE_CHECK_ENABLED = False
        h.patch("relist", lambda *a, **k: trade.RELISTED)
        ok, exc = run(trade.relist_rows, [1])
        check("--no-space-check: a FULL inventory still runs",
              ok is True and not h.said("slot(s) free"),
              f"{ok!r} / {h.out()[:300]}")
    finally:
        trade.SPACE_CHECK_ENABLED = saved
        trade._SPACE_CHECKED = False

check("the flag is left enabled for the suites after this one",
      trade.SPACE_CHECK_ENABLED is True, f"{trade.SPACE_CHECK_ENABLED!r}")


raise SystemExit(summary())
