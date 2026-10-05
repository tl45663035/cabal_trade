import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_rows_by_price_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import buy

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


CHAOS = "Chaos Core"
ENABLE = calibration.margin_for_rows(CHAOS, 1)
TABLE = [(0, 10), (699_999, 10), (700_000, 10), (705_000, 10), (709_999, 10), (710_000, 10), (715_000, 10),
         (716_000, 10), (719_999, 10), (720_000, 8), (725_000, 8), (727_000, 8), (729_999, 8), (730_000, 5),
         (734_999, 5), (735_000, 5), (739_999, 5), (740_000, 2), (745_000, 2), (749_999, 2), (750_000, 0),
         (777_777, 0)]
seen = [(price, calibration.rows_by_price(CHAOS, price)) for price, _rows in TABLE]
check("the price table: each line holds until the next line's price",
      all(got == rows for (price, rows), (_p, got) in zip(TABLE, seen)),
      ", ".join(f"{p:,}->{r}" for p, r in seen))
check("only Chaos Core has a price table", calibration.rows_by_price("Divine Stone", 700_000) is None
      and calibration.price_ladder("Force Core(High)") is None)

check(f"the margin switches the table on at {ENABLE:,}", ENABLE == 5_000, f"{ENABLE}")
check("a margin under that means 0 rows at any price",
      calibration.rows_wanted(CHAOS, ENABLE - 1, 690_000) == 0
      and calibration.rows_wanted(CHAOS, ENABLE - 1, 740_000) == 0)
check("a margin of 5,000 follows the table: 10 rows at 700,000, 2 at 740,000",
      calibration.rows_wanted(CHAOS, 5_000, 700_000) == 10
      and calibration.rows_wanted(CHAOS, 5_000, 740_000) == 2)
check("a margin of 10,000 or more follows the same table: 8 rows at 721,724, 0 from 750,000",
      calibration.rows_wanted(CHAOS, 12_000, 721_724) == 8
      and calibration.rows_wanted(CHAOS, 12_000, 750_000) == 0)
check("a price that did not read buys nothing", calibration.rows_wanted(CHAOS, 12_000, None) == 0)
divine = calibration.rows_by_margin("Divine Stone", 12_000)
check("other cores keep their margin rows", calibration.rows_wanted("Divine Stone", 12_000, 690_000) == divine,
      f"{divine}")
check("Chaos Core can want as many as 10 rows", calibration.rows_wanted_at_most(CHAOS) == 10)
check("every Chaos order needs only the margin that switches the table on, whatever the row",
      all(calibration.margin_for_rows(CHAOS, rows) == ENABLE for rows in range(1, 11)))
check("other cores keep their per-row margins",
      calibration.margin_for_rows("Divine Stone", 1) == 5_000
      and calibration.margin_for_rows("Divine Stone", 4) == 10_000)

said = io.StringIO()
with contextlib.redirect_stdout(said):
    full = driver.margin_says_buy(CHAOS, 8, 12_000, 721_724)
    short = driver.margin_says_buy(CHAOS, 7, 12_000, 721_724)
    thin = driver.margin_says_buy(CHAOS, 5, 4_000, 700_000)
check("with 8 rows held at 721,724 the table wants no more", full is None, said.getvalue().splitlines()[0])
check("with 7 held it buys, needing a margin of 5,000 on every order", short == ENABLE,
      said.getvalue().splitlines()[1])
check("a 4,000 margin stops it however cheap the core is", thin is None, said.getvalue().splitlines()[2])

orders = []


def take(job, want, batch, on_margin=True, verbose=True):
    if not orders:
        return False
    qty, each = orders.pop(0)
    job["bought"] += qty
    job["paid"] += qty * each
    return True


saved = (driver.take_offers, driver.note_step)
driver.take_offers = take
driver.note_step = lambda *a, **k: None
try:
    orders[:] = [(99, 730_000), (51, 731_000), (150, 732_000)]
    j = {"core": CHAOS, "target": 300, "want_max": None, "bought": 0, "paid": 0, "orders": 0,
         "sells_at": 745_000, "gap": ENABLE}
    with contextlib.redirect_stdout(io.StringIO()) as out:
        driver.buy_cores(j)
    check("a restock goes through to its target whatever the table says about its average",
          j["bought"] == 300 and not orders, f"bought {j['bought']}")
finally:
    driver.take_offers, driver.note_step = saved

gaps = []
saved = buy.buy_row_one
buy.buy_row_one = lambda *a, **k: gaps.append(k.get("gap")) or {
    "bought": 10, "spent": 10 * 720_000, "balance": 1, "balance_seen": True, "packs": 10,
    "unit_price": 720_000, "price": 720_000}
try:
    for core, bought in ((CHAOS, 0), (CHAOS, 100), ("Force Core(High)", 100)):
        gaps.clear()
        j = {"core": core, "slot": calibration.favourite_slot_of(core), "target": 300, "want_max": None,
             "sells_at": 733_000, "gap": ENABLE, "leave": 1, "steps_max": 1, "take_all": 1, "orders": 0,
             "bought": bought, "paid": bought * 715_000}
        with contextlib.redirect_stdout(io.StringIO()):
            driver.take_offers(j, 10, 1, verbose=False)
        check(f"{core} with {bought} already bought: the order is checked against the margin before buying",
              gaps == [ENABLE], str(gaps))
finally:
    buy.buy_row_one = saved

clicks = []
saved = (buy.get_price.get_price, calibration.click, buy.await_dialog, buy.show_work_tab)
calibration.click = lambda x, y, *a, **k: clicks.append((x, y))
buy.await_dialog = lambda timeout=None: False
buy.show_work_tab = lambda: None
try:
    for each, bought in ((729_000, False), (728_000, True)):
        clicks.clear()
        buy.get_price.get_price = lambda slot, verbose=True, search=True, **k: {
            "name": CHAOS, "qty": 200, "price": each, "unit_price": each}
        err = None
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                buy._buy_row_one(calibration.favourite_slot_of(CHAOS), 150, sells_at=733_000, gap=ENABLE,
                                 balance=10 ** 12)
            except buy.Refused as exc:
                err = str(exc)
        margin = 733_000 - each
        if bought:
            check(f"a row leaving {margin:,} is taken: it goes on to the Buy button", clicks != []
                  and "wanted" not in (err or ""), f"{err}")
        else:
            check(f"a row leaving only {margin:,} is not bought, first row or not: refused before any click",
                  clicks == [] and "against the 5,000 wanted" in (err or ""), f"{err}")
finally:
    buy.get_price.get_price, calibration.click, buy.await_dialog, buy.show_work_tab = saved

restocks = []
saved = (driver.resupply_chaos, driver.war.avoid)
driver.war.avoid = lambda *a, **k: None
driver.resupply_chaos = lambda model, slot, have, first, last, verbose=True, rows=None: restocks.pop(0)
try:
    slot = calibration.favourite_slot_of(CHAOS)
    restocks[:] = [{"rows": [9], "bought": 150, "paid": 150 * 731_000, "diff": 9_000},
                   {"rows": [10], "bought": 150, "paid": 150 * 731_000, "diff": 9_000}]
    done = []
    with contextlib.redirect_stdout(io.StringIO()) as out:
        driver.resupply_core_rows(None, slot, 4, 10, {}, 1, 25, done)
    check("row 1 said 10 rows, the restock averaged 731,000 (5 rows): with 5 now held, no second restock",
          len(done) == 1 and len(restocks) == 1, out.getvalue().strip().splitlines()[-1])

    restocks[:] = [{"rows": [9], "bought": 150, "paid": 150 * 712_000, "diff": 9_000},
                   {"rows": [10], "bought": 150, "paid": 150 * 712_000, "diff": 9_000},
                   {"rows": [11], "bought": 150, "paid": 150 * 712_000, "diff": 9_000}]
    done = []
    with contextlib.redirect_stdout(io.StringIO()) as out:
        driver.resupply_core_rows(None, slot, 7, 8, {}, 1, 25, done)
    check("row 1 said 8 rows, the restocks averaged 712,000 (10 rows): it goes on to 10",
          len(done) == 3, out.getvalue().strip().splitlines()[-1])
finally:
    driver.resupply_chaos, driver.war.avoid = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
