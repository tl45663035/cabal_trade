"""The x3 recipe's remainder sweep, driven rather than grepped.

The x3 recipe consumes three Cores a craft, so any holding that is not a
multiple of three leaves 1 or 2 behind on CHAOS_WORK_TAB. That tab is checked
by a gate that runs BEFORE chaos_pass, so leaving them there stops the next
cycle before it reaches the only code that could absorb them. The sweep re-runs
the craft with the x1 recipe to clear them.

Everything here is fakes -- no game input, no OCR, no screen. DRIVES NOTHING.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "craft_sweep_test.db")

sys.argv = ["craft_sweep_test"]
import trade as m  # noqa: E402

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def section(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


class Craft:
    """A craft window that consumes material the way the game does.

    `held` falls by the selected recipe's cost per craft on Request All, which
    is what makes the remainder arithmetic real rather than asserted.
    """

    def __init__(self, held=35, recipe_lands=True, reopens=True,
                 counter_reads=True):
        self.held = held
        self.recipe_lands = recipe_lands   # does the recipe click select it?
        self.reopens = reopens             # does the window come back?
        self.counter_reads = counter_reads
        self.selected = None               # material cost now selected
        self.log = []
        self.open = True

    # -- the readers -------------------------------------------------------
    def window_open(self, source=None):
        return self.open

    def counter(self, source=None):
        if not self.counter_reads:
            return None
        return (self.held, self.selected or 1)

    def held_only(self, source=None):
        got = self.counter()
        return None if got is None else got[0]

    # -- the actions -------------------------------------------------------
    def escape(self):
        self.log.append("escape")
        self.open = False
        # Reopening is the only thing that collapses the tier tree again.
        self.resting = True

    def reopen(self, timeout=8.0, verbose=True):
        self.log.append("reopen")
        self.open = bool(self.reopens)
        return self.open

    def select(self, verbose=True):
        self.log.append(f"select:{m.CHAOS_RECIPE}")
        want = m.craft_material_cost()
        self.selected = want if self.recipe_lands else 3
        return True

    def click(self, x, y):
        if (x, y) == m.CRAFT_REQUEST_ALL:
            self.log.append(f"request:{self.selected}")
            per = self.selected or 1
            self.held -= (self.held // per) * per
        elif (x, y) == m.CRAFT_COMPLETE_ALL:
            self.log.append("complete")
        else:
            self.log.append(f"click:{x},{y}")

    def tab(self, tab, origin=None, timeout=5.0):
        self.log.append(f"tab:{tab}")
        return True


def drive(craft):
    """Run craft_chaos_sets against `craft`, restoring every patch."""
    saved = {n: getattr(m, n) for n in (
        "craft_window_open", "craft_material_counter", "craft_material_held",
        "select_chaos_recipe", "click", "press_escape", "open_craft_window",
        "inventory_origin", "select_inventory_tab", "grab", "record",
        "craft_settle_seconds")}
    slept = m.time.sleep
    try:
        m.craft_window_open = craft.window_open
        m.craft_material_counter = craft.counter
        m.craft_material_held = craft.held_only
        m.select_chaos_recipe = craft.select
        m.click = craft.click
        m.press_escape = craft.escape
        m.open_craft_window = craft.reopen
        m.inventory_origin = lambda *a, **k: (0, 0)
        m.select_inventory_tab = craft.tab
        m.grab = lambda *a, **k: None
        m.record = lambda *a, **k: None
        m.craft_settle_seconds = lambda made: 0.0
        m.time.sleep = lambda s: None
        return m.craft_chaos_sets(verbose=False)
    finally:
        m.time.sleep = slept
        for n, v in saved.items():
            setattr(m, n, v)


WAS = m.CHAOS_RECIPE
m.CHAOS_RECIPE = 2                      # the x3, which is what leaves a remainder

section("35 Cores under the x3: 33 craft, 2 are swept")

c = Craft(held=35)
made = drive(c)

# The live 2026-08-15 observation this was built from: 35 held, 33 consumed,
# 2 left over.
check("request:3" in c.log,
      f"the main craft requests the x3 recipe (log {c.log})")
check(c.log.count("request:1") == 1,
      f"and the remainder is then swept with the x1, once (log {c.log})")
check(c.held == 0, f"leaving nothing in the tab, got {c.held}")
check(made == 35,
      f"all 35 Cores are reported as crafted, not just the 33 the x3 took "
      f"(got {made})")

section("the sweep runs AFTER the main craft is collected, not before")

# Both halves matter. Reopening the window with the main craft still queued
# risks that queue; collecting before the tab is fixed drops Sets on whatever
# tab is showing, which is the failure the tab block exists to prevent.
order = c.log


def first(entry, log=None):
    """Index of `entry`, or -1. NEVER raises.

    list.index() throws when the entry is missing, and the entry is missing
    exactly when the code under test is broken -- so the naive version turns
    every real failure into a crash, which the runner reports as an error
    rather than as the specific check that caught it. chaos_pass_test has
    carried that bug at its line 365 for weeks ("'warlag' is not in list").
    """
    log = order if log is None else log
    for i, got in enumerate(log):
        if got == entry or (entry.endswith(":") and got.startswith(entry)):
            return i
    return -1


def last(prefix, log=None):
    log = order if log is None else log
    found = [i for i, e in enumerate(log) if e.startswith(prefix)]
    return found[-1] if found else -1


check(-1 < first("complete") < first("escape"),
      f"the main Complete All precedes the sweep's escape (log {order})")
check(-1 < first("escape") < first("request:1"),
      f"and the escape precedes the sweep's Request All (log {order})")
check(-1 < last("tab:") < last("complete"),
      f"the work tab is selected before the LAST Complete All, so the swept "
      f"Set lands on it (log {order})")

section("the tree is put back to resting before the second selection")

# CRAFT_RECIPES' points assume every tier is collapsed. The first selection has
# already expanded one, so clicking the tier point again would collapse it and
# the recipe click would land on whatever moved into that row.
check(-1 < first("escape") < first("reopen") < last("select:"),
      f"escape, then reopen, then select -- in that order (log {order})")

section("a sweep that cannot verify its recipe does not craft")

# select_chaos_recipe returns True on having CLICKED, not on having selected.
c2 = Craft(held=35, recipe_lands=False)
made2 = drive(c2)
check("request:1" not in c2.log,
      f"the x1 was never requested, because the counter still said 3 "
      f"(log {c2.log})")
check(c2.held == 2, f"so the 2 Cores are left for a human, got {c2.held}")
check(made2 == 33, f"and only the 33 really crafted are reported, got {made2}")

section("a window that will not reopen is not clicked at blindly")

c3 = Craft(held=35, reopens=False)
made3 = drive(c3)
check("request:1" not in c3.log,
      f"nothing is requested into a closed window (log {c3.log})")
check(c3.held == 2, "the remainder survives, to be reported not consumed")

section("an unreadable counter refuses rather than guessing")

c4 = Craft(held=35, counter_reads=False)
made4 = drive(c4)
check("request:1" not in c4.log,
      f"no sweep is attempted when the counter will not read (log {c4.log})")

section("no remainder means no sweep at all")

c5 = Craft(held=36)              # 36 is a multiple of 3
made5 = drive(c5)
check("escape" not in c5.log,
      f"the window is not reopened when nothing is left over (log {c5.log})")
check("request:1" not in c5.log, "and the x1 is never selected")
check(made5 == 36 and c5.held == 0, f"36 craft cleanly, got {made5}")

section("the operator's recipe setting is restored whatever happens")

for label, craft in (("after a clean sweep", c), ("after a refusal", c2),
                     ("after a dead window", c3)):
    check(m.CHAOS_RECIPE == 2, f"CHAOS_RECIPE is still 2 {label}")

m.CHAOS_RECIPE = WAS

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
