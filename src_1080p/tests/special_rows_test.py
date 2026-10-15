import contextlib
import copy
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_special_rows_")) / "sales.db")
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


CHAOS, DIVINE = "Chaos Core", "Divine Stone"
REAL_SHARED = calibration.load_shared
ON = copy.deepcopy(REAL_SHARED())
ON["resupply"]["special_row"]["enabled"] = True
calibration.load_shared = lambda: copy.deepcopy(ON)
saved_turn = driver.holds_turn
driver.holds_turn = lambda: True


def priced(price):
    m.LAST_MARKET[m.item_key(CHAOS)] = price


def board(special, price):
    model = m.RowModel().seed({})
    for index in range(1, special + 1):
        model._slots[index] = m.Row(CHAOS, qty=10, price=price)
    return model


try:
    check("special rows have no price table any more", calibration.special_ladder(CHAOS) is None,
          str(calibration.special_ladder(CHAOS)))
    for price in (690_000, 725_000, 729_999, 730_000, 740_000, 745_001, 800_000):
        priced(price)
        check(f"Chaos Core at {price:,}: 1 special row", driver.special_rows(CHAOS) == 1, str(driver.special_rows(CHAOS)))
    saved = m.market_anchor
    m.market_anchor = lambda name: 0
    try:
        check("no Chaos Core price read yet: still 1", driver.special_rows(CHAOS) == 1, str(driver.special_rows(CHAOS)))
    finally:
        m.market_anchor = saved
    check("Divine Stone: 1 as well", driver.special_rows(DIVINE) == 1, str(driver.special_rows(DIVINE)))

    priced(740_000)
    for held, want in ((0, 1), (1, 0), (3, 0)):
        wanted = driver.special_wanted(board(held, 725_000), 1, 30)
        check(f"{held} special row(s) listed: {want} more wanted", len(wanted) == want and set(wanted) <= {CHAOS},
              str(wanted))
    model = board(0, 725_000)
    free = [i for i in model.empty() if 1 <= i <= 30]
    check("one free row is kept for the special row, not for resupply",
          len(driver.buying_rows(model, 1, 30)) == len(free) - 1, f"{len(free)} free")

    said = io.StringIO()
    saved = (driver.shop_ready, driver.war.avoid, driver.alz_too_low)
    driver.shop_ready = lambda *a, **k: False
    driver.war.avoid = lambda *a, **k: None
    driver.alz_too_low = lambda: False
    m.LAST_CHECKED[m.item_key("Chaos Core Set")] = 732_407
    try:
        with contextlib.redirect_stdout(said):
            driver.resupply_special(board(0, 725_000), 1, 30, verbose=True)
    finally:
        driver.shop_ready, driver.war.avoid, driver.alz_too_low = saved
    under = ON["resupply"]["special_row"]["under_set_market"][CHAOS]
    spread = ON["resupply"]["special_row"]["under_set_spread"]
    check("the header says 1 of 1 and the price it goes out at",
          f"-- Chaos Core special row 1 of 1: {driver.special_qty()} at {under - spread:,} to {under + spread:,} under "
          f"the latest Chaos Core Set market of 732,407 --" in said.getvalue(),
          said.getvalue().strip().splitlines()[1] if len(said.getvalue().strip().splitlines()) > 1 else said.getvalue())
finally:
    calibration.load_shared = REAL_SHARED
    driver.holds_turn = saved_turn

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
