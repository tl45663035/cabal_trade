import contextlib
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
FIXED = int(driver.special_conf().get("rows") or 1)


def priced(price):
    m.LAST_MARKET[m.item_key(CHAOS)] = price


check("the special-row price table comes from config",
      calibration.special_ladder(CHAOS) == [(0, 4), (730_000, 5), (740_000, 6)], str(calibration.special_ladder(CHAOS)))
for price, rows in ((690_000, 4), (705_000, 4), (716_000, 4), (725_000, 4), (729_999, 4), (730_000, 5), (735_000, 5),
                    (739_999, 5), (740_000, 6), (745_000, 6), (745_001, 6), (800_000, 6)):
    priced(price)
    got = driver.special_rows(CHAOS)
    check(f"Chaos Core at {price:,}: {rows} special row(s)", got == rows, str(got))

saved = m.market_anchor
m.market_anchor = lambda name: 0
try:
    check(f"no Chaos Core price read yet: the fixed {FIXED} from config", driver.special_rows(CHAOS) == FIXED,
          str(driver.special_rows(CHAOS)))
finally:
    m.market_anchor = saved
priced(740_000)
check(f"Divine Stone has no price table: always the fixed {FIXED}", driver.special_rows(DIVINE) == FIXED,
      str(driver.special_rows(DIVINE)))


def board(special, price):
    model = m.RowModel().seed({})
    for index in range(1, special + 1):
        model._slots[index] = m.Row(CHAOS, qty=1, price=price)
    return model


for held, price, want in ((4, 725_000, 0), (4, 730_000, 1), (4, 740_000, 2), (5, 740_000, 1), (6, 725_000, 0),
                          (6, 745_001, 0)):
    priced(price)
    model = board(held, price)
    wanted = driver.special_wanted(model, 1, 30)
    check(f"{held} special row(s) listed, Chaos Core at {price:,}: {want} more wanted", len(wanted) == want
          and set(wanted) <= {CHAOS}, str(wanted))
priced(740_000)
model = board(4, 740_000)
free = [i for i in model.empty() if 1 <= i <= 30]
check("the free rows the table still wants are kept for the special rows, not for resupply",
      len(driver.buying_rows(model, 1, 30)) == len(free) - 2, f"{len(free)} free")

said = io.StringIO()
saved = (driver.shop_ready, driver.war.avoid)
driver.shop_ready = lambda *a, **k: False
driver.war.avoid = lambda *a, **k: None
try:
    priced(731_000)
    with contextlib.redirect_stdout(said):
        driver.resupply_special(board(4, 731_000), 1, 30, verbose=True)
finally:
    driver.shop_ready, driver.war.avoid = saved
check("the log names the price behind the count", "special row 5 of 5 (Chaos Core at 731,000 a core)"
      in said.getvalue(), said.getvalue().strip().splitlines()[0] if said.getvalue().strip() else "")
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
