import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_lost_craft_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import craft
import driver
import ledger
import row_model

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


calls = []
slots = {"now": set()}
saved = (craft.open_craft, craft.select_recipe, craft.show_work_tab, craft.request_all, craft.await_drain,
         craft.complete_all, craft.compress, calibration.occupied_slots)
craft.open_craft = lambda verbose=True: calls.append("open the craft window")
craft.select_recipe = lambda core=None, verbose=True: calls.append("select the recipe")
craft.show_work_tab = lambda: calls.append("show tab 4")
craft.request_all = lambda verbose=True: calls.append("Request All")
craft.await_drain = lambda before, verbose=True: calls.append("wait") or before
craft.complete_all = lambda verbose=True: calls.append("Complete All") or slots.update(now={(1, 1)})
craft.compress = lambda slot, verbose=True: calls.append(f"compress {tuple(slot)}")
calibration.occupied_slots = lambda *a, **k: set(slots["now"])
try:
    with contextlib.redirect_stdout(io.StringIO()):
        out = craft.craft_sets("Chaos Core", verbose=True, held=18)
    check("tab 4 is selected once, after Request All and before Complete All, as before the 2026-10-07 test",
          calls.count("show tab 4") == 1
          and calls.index("Request All") < calls.index("show tab 4") < calls.index("Complete All"), str(calls))
    check("the Sets that arrive at Complete All are found and merged as before",
          out["slot"] == (1, 1) and calls[-1] == "compress (1, 1)", f"{out}; {calls[-1]}")
finally:
    (craft.open_craft, craft.select_recipe, craft.show_work_tab, craft.request_all, craft.await_drain,
     craft.complete_all, craft.compress, calibration.occupied_slots) = saved


class Model:
    def __init__(self):
        self.held, self.released, self.placed = [], [], []

    def empty(self):
        return [7]

    def list_slot(self, row, col, **kw):
        raise row_model.NothingLoaded(f"nothing loaded into the shop slot from ({row},{col}) after 1 ctrl-click(s).")

    def hold_work(self, slot, what=None):
        self.held.append((tuple(slot), what))

    def release_work(self, slot):
        self.released.append(tuple(slot))

    def place(self, index, row):
        self.placed.append(index)


def job():
    return {"core": "Chaos Core", "set": "Chaos Core Set", "bought": 18, "paid": 18 * 733_845, "work": (5, 2),
            "crafted": 18, "sells_at": 738_877, "rows": [], "listed": 0, "core_price": 733_845}


tab4 = {"read": set()}
saved = (driver.back_to_the_shop, driver.register_tab, driver.work_tab_slots, calibration.click,
         driver.time.sleep, calibration.price_floor)
driver.back_to_the_shop = lambda verbose=True: True
driver.register_tab = lambda verbose=True: None
driver.work_tab_slots = lambda verbose=True: set(tab4["read"])
calibration.click = lambda *a, **k: None
driver.time.sleep = lambda s: None
calibration.price_floor = lambda name: (0, "Chaos Core")
try:
    model, work = Model(), job()
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            full = driver.list_sets(model, work, 1, 25, verbose=False)
            raised = None
        except driver.NotReady as exc:
            full, raised = None, exc
    check("the 13:00 case: the craft's tab 4 slot (5, 2) reads empty, so the job is closed, not carried on",
          raised is None and full is False and model.released == [(5, 2)] and model.held == [],
          f"{raised}; released {model.released}, held {model.held}")
    check("and the log names it a lost craft with what went in",
          "LOST CRAFT" in said.getvalue() and "crafted from 18 Chaos Core(s)" in said.getvalue(),
          said.getvalue().strip()[-220:])

    tab4["read"] = {(5, 2)}
    model, work = Model(), job()
    with contextlib.redirect_stdout(io.StringIO()) as said:
        try:
            driver.list_sets(model, work, 1, 25, verbose=False)
            raised = None
        except driver.NotReady as exc:
            raised = exc
    check("a Set that is still in its slot when nothing loads keeps the job for the next pass, as before",
          raised is not None and model.held == [((5, 2), "Chaos Core Set")] and model.released == []
          and "LOST CRAFT" not in said.getvalue(), f"{raised}; held {model.held}")

    tab4["read"] = {(r, c) for r in range(1, row_model.GRID + 1) for c in range(1, row_model.GRID + 1)}
    model, work = Model(), job()
    with contextlib.redirect_stdout(io.StringIO()) as said:
        try:
            driver.list_sets(model, work, 1, 25, verbose=False)
            raised = None
        except driver.NotReady as exc:
            raised = exc
    check("a tab 4 that reads full (a withdrawal in transit) is not trusted to say the slot is empty",
          raised is not None and model.held == [((5, 2), "Chaos Core Set")] and "LOST CRAFT" not in said.getvalue(),
          f"{raised}; held {model.held}")
    row_model.WORK_TAB_STALE = False
finally:
    (driver.back_to_the_shop, driver.register_tab, driver.work_tab_slots, calibration.click,
     driver.time.sleep, calibration.price_floor) = saved

check("crafting itself is never switched off by a lost craft",
      not hasattr(driver, "_LOST_CRAFT") and "not crafted again" not in Path(driver.__file__).read_text(encoding="utf-8"))
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
