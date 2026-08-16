"""The work-tab gate has three states, and each answer matters differently.

relist() identifies the item a cancel returned by DIFFING the inventory before
and after. That diff is only unambiguous while the work tab starts EMPTY --
which is what the gate's own refusal has always said: "leftover items make it
impossible to tell which ones came back."

Both ways of getting this wrong have now happened on this account:

  * RAISING FatalAbort on a tab holding the restock's own paid-for Sets wedged
    the run permanently. A restock that bought and could not list banked the
    Sets on purpose so the next pass would convert them -- and the gate then
    stopped the run before that pass could ever run, and stayed fatal on every
    restart, because a new process meets the same dirty tab before
    restock_pass is reached and the process-lifetime carry record is lost with
    it.

  * RETURNING TRUE for that same case -- the fix for the above -- let a batch
    relist on top of 7 carried Sets sitting in slot (1,1). The cancelled-item
    diff came back with 13 slots, `returned[0]` picked the SETS rather than the
    12 Epic Boosters that had just come back, and the Boosters were left
    cancelled and unlisted. The quantity cross-check refused the wrong slot, so
    nothing wrong was listed, but ~648,000,000 Alz of stock was stranded.

So the three states are asserted separately, and the middle one is asserted to
be FALSE specifically -- neither True nor a raise.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


class Tab:
    """The work tab in a chosen state, with the carry registry set."""

    def __init__(self, occupied, carried):
        self.occupied = occupied
        self.carried = carried
        self.saved = {}

    def __enter__(self):
        for n in ("inventory_origin", "select_inventory_tab", "park_cursor",
                  "grab", "occupied_slots", "carried_total", "record"):
            self.saved[n] = getattr(m, n)
        m.inventory_origin = lambda source=None: (100, 100)
        m.select_inventory_tab = lambda tab, origin, verbose=True: True
        m.park_cursor = lambda settle=0.0: None
        m.grab = lambda: object()
        m.occupied_slots = lambda shot, origin: list(self.occupied)
        m.carried_total = lambda: self.carried
        m.record = lambda *a, **k: None
        return self

    def __exit__(self, *exc):
        for n, v in self.saved.items():
            setattr(m, n, v)


def call():
    """Run the gate; return True/False, or the exception type name."""
    try:
        return m.ensure_work_tab_empty(verbose=False)
    except Exception as exc:            # noqa: BLE001
        return type(exc).__name__


# -- 1. an empty tab is fine ----------------------------------------------
with Tab(occupied=[], carried=0):
    check(call() is True, "an empty work tab passes")
with Tab(occupied=[], carried=7):
    check(call() is True,
          "an empty tab passes even with Sets on the books -- the carry is "
          "bookkeeping, the TAB is what this gate is about")


# -- 2. dirty WITH carry: refuse the cycle, do not stop the run -----------
DIRTY = [(1, 1), (1, 2), (1, 3)]
with Tab(occupied=DIRTY, carried=7):
    got = call()
check(got is False,
      f"a tab holding the restock's own Sets must return False -- not True "
      f"(which relists on top of an ambiguous diff and strands the cancelled "
      f"item) and not a raise (which wedges the run and every restart). "
      f"got {got!r}")

# The two ways this has actually been wrong, named so a regression is obvious.
with Tab(occupied=DIRTY, carried=7):
    got = call()
check(got is not True,
      "MUST NOT be True: that is the 2026-08-09 bug that stranded Epic "
      "Booster x12 by picking the carried Sets out of the diff")
check(got != "FatalAbort",
      "MUST NOT raise FatalAbort: that is the earlier bug that wedged the run "
      "permanently, including across restarts")


# -- 3. dirty with NO carry: this really is a strand ----------------------
with Tab(occupied=DIRTY, carried=0):
    got = call()
check(got == "FatalAbort",
      f"a tab holding something nobody can account for still stops the run -- "
      f"the script cannot identify it and must not guess. got {got!r}")


# -- 4. the callers must be able to act on False --------------------------
# A False that every caller ignores is the same as a True.
import inspect  # noqa: E402

for fn in (m.relist_rows, m._relist_cycle):
    src = inspect.getsource(fn)
    # CALL sites only -- "ensure_work_tab_empty(" with a paren. Matching the
    # bare name also matches the comments that discuss it, which is how the
    # first version of this check passed on a comment line.
    calls = [ln for ln in src.splitlines()
             if "ensure_work_tab_empty(" in ln and not ln.strip().startswith("#")]
    check(bool(calls), f"{fn.__name__} calls the gate at all")
    for line in calls:
        check("if not " in line or line.strip().startswith("if "),
              f"{fn.__name__} must branch on the result -- a False every "
              f"caller ignores is the same as a True. got: {line.strip()}")

check("FatalAbort" in inspect.getsource(m.ensure_work_tab_empty),
      "the strand path still raises")
check("return False" in inspect.getsource(m.ensure_work_tab_empty),
      "and the carry path still has a False to return")


print(f"worktab_gate_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
