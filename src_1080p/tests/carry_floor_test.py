import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_carry_floor_"))
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(SCRATCH / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import ledger
import row_model as m

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
calibration.VERIFIED = SCRATCH / "verified_costs.json"
calibration._VERIFIED = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


HIGHEST = "Force Core(Highest)"
ULTIMATE = "Force Core (Ultimate)"
HIGH = "Force Core(High)"
start_up = calibration.market_unit(
    calibration.FAVOURITE_ITEMS[str(calibration.pair_slot(calibration.favourite_slot_of(HIGHEST)))])
check("calibration.json holds a start-up market price for the Force Core(Highest) pair", start_up > 0, f"{start_up:,}")

calibration.VERIFIED.write_text(json.dumps({str(calibration.favourite_slot_of(HIGHEST)): {
    "item": HIGHEST, "each": 440_000, "at": "2026-10-12T23:33:53"}}), encoding="utf-8")
calibration._VERIFIED = None
carried = m.Row("Force Core(Highest", qty=87, price=440_000)
before = driver.listing_floor(carried)
check("before a fresh start, a carried row is floored at what an earlier run paid",
      before[0] == 440_000, str(before))

said = io.StringIO()
with contextlib.redirect_stdout(said):
    driver.fresh_floors()
check("a fresh start says which kept costs it dropped",
      "Force Core(Highest) 440,000" in said.getvalue(), said.getvalue().strip())
check("a fresh start empties the kept costs on disk",
      json.loads(calibration.VERIFIED.read_text(encoding="utf-8")) == {})
after = driver.listing_floor(carried)
check("after a fresh start, a carried row is floored at the start-up market price",
      after[0] == start_up and "as last bought" not in after[1], str(after))

calibration.note_verified_cost(HIGHEST, 300_000)
bought = m.Row(HIGHEST, qty=200, price=360_000, buy_cost=300_000)
check("a row bought this run is floored at what it paid at once",
      driver.listing_floor(bought) == (300_000, "it cost 300,000 each"), str(driver.listing_floor(bought)))
check("a row of that item that lost its own cost takes this run's price",
      driver.listing_floor(m.Row(HIGHEST, qty=5, price=360_000))[0] == 300_000)

calls = []
saved = (driver.fresh_floors, driver.do_relist, driver.do_collect, driver.SESSION_MEASURED, driver.REPAIR)
driver.fresh_floors = lambda verbose=True: calls.append("fresh")
driver.do_relist = lambda *a, **k: calls.append("relist")
driver.do_collect = lambda *a, **k: calls.append("collect")
try:
    driver.SESSION_MEASURED = False
    driver._dispatch([])
    fresh_launch = list(calls)
    calls.clear()
    driver.SESSION_MEASURED = True
    driver._dispatch([])
    relaunch = list(calls)
    calls.clear()
    driver.SESSION_MEASURED = False
    driver._dispatch(["collect"])
    repair = list(calls)
finally:
    (driver.fresh_floors, driver.do_relist, driver.do_collect, driver.SESSION_MEASURED,
     driver.REPAIR) = saved
check("a launch by hand or a supervisor's first launch starts fresh",
      fresh_launch == ["fresh", "relist"], str(fresh_launch))
check("a supervisor relaunch after a crash keeps the crashed run's floors",
      relaunch == ["relist"], str(relaunch))
check("a recovery repair keeps them too", repair == ["collect"], str(repair))

screen = {
    3: m.Row("Force Core(Highest", qty=87, price=440_000),
    5: m.Row("Upgrade Core (Ultimate", qty=1, price=398_848),
    8: m.Row("Force Core (Ultimate", qty=182, price=490_490),
    10: m.Row(HIGH, qty=190, price=274_000),
    12: m.Row("Force Core(Highest", qty=53, price=430_000),
    14: m.Row("Force Core(Highest", qty=40, price=420_000),
}
crashed = {
    3: m.Row(HIGHEST, qty=87, price=440_000, buy_cost=434_681),
    5: m.Row("Upgrade Core (Ultimate)", qty=1, price=398_848),
    8: m.Row(ULTIMATE, qty=182, price=490_490, buy_cost=474_998, floor_at=474_998),
    10: m.Row(HIGHEST, qty=190, price=274_000, buy_cost=440_000),
    12: m.Row(HIGHEST, qty=53, price=440_000, buy_cost=440_000),
    14: m.Row(HIGHEST, qty=60, price=420_000, buy_cost=410_000),
}


def seeded(continuing):
    ledger.board_save(crashed)
    driver.SESSION_MEASURED = continuing
    looked = {}
    real = (m.RowModel.scroll_to, m.RowModel.read_with_button, m.RowModel.home, m.last_seat_placed,
            driver.register_tab, driver.row_from_screen)
    m.RowModel.scroll_to = lambda self, index, verbose=True: looked.__setitem__("at", index)
    m.RowModel.read_with_button = lambda self: (
        (f"row {looked['at']}", m.CHANGE_WORD) if looked["at"] in screen else ("", m.REGISTER_WORD))
    m.RowModel.home = lambda self, verbose=True: 1
    m.last_seat_placed = lambda: True
    driver.register_tab = lambda verbose=True: None
    driver.row_from_screen = lambda text, model: (text, screen[int(text.split()[1])].copy())
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            model = driver.seed(verbose=True)
    finally:
        (m.RowModel.scroll_to, m.RowModel.read_with_button, m.RowModel.home, m.last_seat_placed,
         driver.register_tab, driver.row_from_screen) = real
        driver.SESSION_MEASURED = saved[3]
    return model, out.getvalue()


model, said = seeded(True)
costs = {i: (model.get(i).buy_cost, model.get(i).floor_at) for i in screen}
check("after a crash, a row the crashed run bought keeps what it paid",
      costs[3] == (434_681, 0), str(costs[3]))
check("its floor comes from what it cost, not an earlier run's file",
      driver.listing_floor(model.get(3)) == (434_681, "it cost 434,681 each"), str(driver.listing_floor(model.get(3))))
check("a row with a floor mark keeps the mark", costs[8] == (474_998, 474_998), str(costs[8]))
check("a row the crashed run had no cost for gets none", costs[5] == (0, 0), str(costs[5]))
check("a different item in that row takes nothing from the crashed run", costs[10] == (0, 0), str(costs[10]))
check("the same item at a different price takes nothing", costs[12] == (0, 0), str(costs[12]))
check("the same lot partly sold keeps its cost", costs[14] == (410_000, 0), str(costs[14]))
check("the log says how many rows kept their cost", "3 row(s) keep the cost and floor the crashed run" in said,
      said.strip().splitlines()[-2:])
on_file = ledger.board_costs()
check("the kept costs are saved again, so a repair before the relaunch does not lose them",
      on_file[3][2] == 434_681 and on_file[8][2:] == (474_998, 474_998), str(on_file.get(3)))

model, said = seeded(False)
costs = {i: (model.get(i).buy_cost, model.get(i).floor_at) for i in screen}
check("a fresh start takes no cost from the run before", all(c == (0, 0) for c in costs.values()), str(costs))
check("and says nothing about kept costs", "keep the cost" not in said)

check("no game input was sent", not TRIPPED, str(TRIPPED))
print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
