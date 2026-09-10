import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


class Tab:

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
    try:
        return m.ensure_work_tab_empty(verbose=False)
    except Exception as exc:
        return type(exc).__name__


with Tab(occupied=[], carried=0):
    check(call() is True, "an empty work tab passes")
with Tab(occupied=[], carried=7):
    check(call() is True,
          "an empty tab passes even with Sets on the books -- the carry is "
          "bookkeeping, the TAB is what this gate is about")


DIRTY = [(1, 1), (1, 2), (1, 3)]
with Tab(occupied=DIRTY, carried=7):
    got = call()
check(got is False,
      f"a tab holding the restock's own Sets must return False -- not True "
      f"(which relists on top of an ambiguous diff and strands the cancelled "
      f"item) and not a raise (which wedges the run and every restart). "
      f"got {got!r}")

with Tab(occupied=DIRTY, carried=7):
    got = call()
check(got is not True,
      "MUST NOT be True: that is the 2026-08-09 bug that stranded Epic "
      "Booster x12 by picking the carried Sets out of the diff")
check(got != "FatalAbort",
      "MUST NOT raise FatalAbort: that is the earlier bug that wedged the run "
      "permanently, including across restarts")


with Tab(occupied=DIRTY, carried=0):
    got = call()
check(got == "FatalAbort",
      f"a tab holding something nobody can account for still stops the run -- "
      f"the script cannot identify it and must not guess. got {got!r}")


import inspect

for fn in (m.relist_rows, m._relist_cycle):
    src = inspect.getsource(fn)
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
