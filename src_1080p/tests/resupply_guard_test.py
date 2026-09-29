import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_resupply_guard_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import row_model as m

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def slot_of(name):
    return next(int(s) for s, item in calibration.FAVOURITE_ITEMS.items() if item == name)


asked = []
saved = (driver.alz_covers, driver.war.avoid, driver.resupply_chaos, driver.resupply_one, driver.craft_route)
driver.alz_covers = lambda what, units, unit_price, verbose=True: asked.append((what, units, unit_price)) or False
driver.war.avoid = lambda *a, **k: 0.0
driver.resupply_chaos = driver.resupply_one = lambda *a, **k: None
try:
    for name in ("Chaos Core", "Divine Stone"):
        if name not in calibration.FAVOURITE_ITEMS.values():
            continue
        driver.craft_route = lambda core: True
        asked.clear()
        slot = slot_of(name)
        driver.resupply_core_rows(m.RowModel().seed({}), slot, 0, 1, {slot: {"unit_price": 746000}}, 1, 30, [],
                                  verbose=False)
        check(f"a {name} resupply buys nothing unless the balance covers {calibration.craft_alz_cores(name)} cores",
              asked and asked[0][1] == 30 == calibration.craft_alz_cores(name) and asked[0][2] == 746000, str(asked))
    driver.craft_route = lambda core: False
    asked.clear()
    slot = slot_of("Force Core(High)")
    driver.resupply_core_rows(m.RowModel().seed({}), slot, 0, 1, {slot: {"unit_price": 172998}}, 1, 30, [],
                              verbose=False)
    check("a resupply that is not crafted still needs only one at row 1's price", asked and asked[0][1] == 1, str(asked))
finally:
    driver.alz_covers, driver.war.avoid, driver.resupply_chaos, driver.resupply_one, driver.craft_route = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
