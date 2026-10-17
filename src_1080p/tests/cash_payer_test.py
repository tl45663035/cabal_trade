import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_cash_payer_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import cashshop
import driver
import ledger

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None

BOOSTER = "Epic Booster (Highest)"
PACK = "Force Gem Package"
PRICE = 100
COUNT = int(calibration.buy_min(BOOSTER))

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


class Shop:
    def __init__(self, gems):
        self.gems = gems
        self.closed = 0
        self.bought = []


SHOP = Shop(0)


def purchase(item, confirm=True, verbose=True, count=1):
    SHOP.bought.append((item, count))
    return {"item": item, "bought": count, "price": PRICE * count, "balance": SHOP.gems,
            "cancelled": False}


def close_cash_shop(verbose=True):
    SHOP.closed += 1
    return True


saved = (driver.answer_pending_use_question, calibration.close_everything, driver.work_tab_slots,
         cashshop.open_cash_shop, cashshop.select_tab, cashshop.cell_for, cashshop.cc_now,
         cashshop.gems_now, cashshop.close_cash_shop, cashshop.purchase, driver.cash_floor,
         ledger.bought, cashshop.items)
driver.answer_pending_use_question = lambda verbose=True: None
calibration.close_everything = lambda verbose=False: None
driver.work_tab_slots = lambda verbose=True: set()
cashshop.open_cash_shop = lambda verbose=True: True
cashshop.select_tab = lambda word=None, verbose=True: (844, 168)
cashshop.cell_for = lambda item, verbose=True: {"item": item, "text": item, "purchase": (1364, 750),
                                                "price": PRICE}
cashshop.cc_now = lambda verbose=True: 800
cashshop.gems_now = lambda verbose=True: SHOP.gems
cashshop.close_cash_shop = close_cash_shop
cashshop.purchase = purchase
driver.cash_floor = lambda job, verbose=True: (48_625_000, "test")
ledger.bought = lambda *a, **k: None
real_items = saved[-1]


def job():
    return {"kind": "cash", "core": BOOSTER, "slot": None, "step": "buy", "count": COUNT, "bought": 0,
            "price": 0, "cc_seen": None, "cc_before_voucher": None, "voucher_paid": 0, "voucher_before": [],
            "voucher_slot": None, "before": [], "after_voucher": None, "gem_before": [], "gem_paid": 0,
            "gem_slot": None, "work": [], "rows": [], "listed": 0}


def buy_step(gems, sold):
    global SHOP
    SHOP = Shop(gems)
    cashshop.items = (lambda: real_items() + [PACK]) if sold else real_items
    j = job()
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            driver.cash_buy_step(j, confirm=True, verbose=True)
            error = None
        except driver.NotReady as exc:
            error = exc
    return j, error, said.getvalue()


check(PACK not in real_items(), f"the {PACK} is off the Cash Shop list, got {real_items()}")
check((cashshop.currency_of(BOOSTER) or {}).get("from") == PACK,
      f"the {BOOSTER} is still paid in gems the {PACK} priced, for its floor")
check(cashshop.payer_of(BOOSTER) is None, f"nothing tops up the gems, got {cashshop.payer_of(BOOSTER)!r}")
real_rows = cashshop.rows_wanted
cashshop.rows_wanted = lambda item: 1 if item == BOOSTER else real_rows(item)
order = cashshop.in_reading_order()
check(PACK not in order, f"the start-up measure no longer looks for the {PACK}, got {order}")
check(BOOSTER in order, f"it still measures the {BOOSTER}, got {order}")

cashshop.items = lambda: real_items() + [PACK]
check(cashshop.payer_of(BOOSTER) == PACK, f"a {PACK} the shop sells still tops the gems up")
check(PACK in cashshop.in_reading_order(), f"and is measured while the shop sells it")
cashshop.items = real_items
cashshop.rows_wanted = real_rows

short = PRICE * COUNT - 66
j, error, said = buy_step(short, sold=False)
check(isinstance(error, driver.NotReady), f"{short} gems with no {PACK} sold stops the job, got {error!r}")
check(str(error) == f"{short:,} gem(s) held is under the {PRICE * COUNT:,} that {COUNT} of them cost, and the "
                   f"Cash Shop sells no {PACK} to top them up; not buying.",
      f"saying why, got {error}")
check(j["step"] == "buy" and j["bought"] == 0 and not SHOP.bought,
      f"nothing is bought and no {PACK} step is taken, got {j['step']} {SHOP.bought}")
check(SHOP.closed >= 1, "the Cash Shop is closed on the way out")
check(f"buying a {PACK}" not in said, f"and the log never says it is buying a {PACK}")

j, error, said = buy_step(short, sold=True)
check(error is None and j["step"] == "gem", f"while the shop sells the {PACK} it still buys one first, got "
                                            f"{j['step']} {error!r}")
check(f"buying a {PACK} and using it first" in said, "and says so")

j, error, said = buy_step(PRICE * COUNT, sold=False)
check(error is None and SHOP.bought == [(BOOSTER, COUNT)] and j["step"] == "land",
      f"gems that cover the row buy it as before, got {SHOP.bought} {j['step']} {error!r}")

(driver.answer_pending_use_question, calibration.close_everything, driver.work_tab_slots,
 cashshop.open_cash_shop, cashshop.select_tab, cashshop.cell_for, cashshop.cc_now,
 cashshop.gems_now, cashshop.close_cash_shop, cashshop.purchase, driver.cash_floor,
 ledger.bought, cashshop.items) = saved

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
