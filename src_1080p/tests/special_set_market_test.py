import contextlib
import copy
import inspect
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_special_set_market_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import get_price
import ledger
import row_model as m

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


CHAOS, SET, DIVINE = "Chaos Core", "Chaos Core Set", "Divine Stone"
REAL_SHARED = calibration.load_shared
ON = copy.deepcopy(REAL_SHARED())
ON["resupply"]["special_row"]["enabled"] = True
held_turn = driver.holds_turn
driver.holds_turn = lambda: True


def shared(change=None):
    conf = copy.deepcopy(ON)
    if change:
        change(conf["resupply"]["special_row"])
    calibration.load_shared = lambda: conf


def markets(core=740_000, sets=732_000):
    m.LAST_CHECKED.clear()
    m.LAST_MARKET.clear()
    m.LAST_MARKET[m.item_key(CHAOS)] = core
    m.LAST_MARKET[m.item_key(SET)] = sets


shared()
try:
    check("the shared config turns the Set price on for Chaos Core only",
          ON["resupply"]["special_row"]["at_set_market"] == {"default": False, CHAOS: True},
          str(ON["resupply"]["special_row"].get("at_set_market")))

    m.LAST_CHECKED.clear()
    m.LAST_MARKET.clear()
    check("nothing checked yet this run: the Chaos special row takes the Set price the start-up read",
          driver.special_price(CHAOS) == (SET, calibration.market_unit(SET)) and calibration.market_unit(SET) > 0,
          str(driver.special_price(CHAOS)))

    markets(sets=731_000)
    check("the margin check's Set price is what the special row lists at",
          driver.special_price(CHAOS) == (SET, 731_000), str(driver.special_price(CHAOS)))

    with contextlib.redirect_stdout(io.StringIO()):
        get_price._priced({"name": SET, "qty": 2, "pack": 1, "total": 1_464_000}, calibration.favourite_slot_of(SET),
                          SET, False)
    check("a Purchase tab read of the Set is a check: 732,000", driver.special_price(CHAOS) == (SET, 732_000),
          str(driver.special_price(CHAOS)))
    check("every Purchase tab price read goes through that check",
          "return _priced(" in inspect.getsource(get_price.get_price), "")

    with contextlib.redirect_stdout(io.StringIO()):
        get_price._priced({"name": CHAOS, "qty": 1, "pack": 1, "total": 745_000}, calibration.favourite_slot_of(CHAOS),
                          CHAOS, False)
    check("a read of the core does not move the Set price", driver.special_price(CHAOS) == (SET, 732_000),
          str(driver.special_price(CHAOS)))

    shared(lambda conf: conf.pop("at_set_market"))
    check("with at_set_market left out, no special row takes the Set price", driver.special_price(CHAOS) == (None, 0),
          str(driver.special_price(CHAOS)))
    shared(lambda conf: conf.update(at_set_market={"default": False, CHAOS: False}))
    check("with Chaos Core switched off, no Set price", driver.special_price(CHAOS) == (None, 0),
          str(driver.special_price(CHAOS)))
    shared(lambda conf: conf.update(at_set_market=True))
    check("a bare true turns it on for every special core", driver.special_price(CHAOS) == (SET, 732_000),
          str(driver.special_price(CHAOS)))
    shared()

    saved_items = dict(calibration.FAVOURITE_ITEMS)
    calibration.FAVOURITE_ITEMS.update({"11": DIVINE, "12": "Divine Stone Set"})
    try:
        check("Divine Stone's special row keeps its old price: its Set price is off by default",
              driver.special_price(DIVINE) == (None, 0), str(driver.special_price(DIVINE)))
    finally:
        calibration.FAVOURITE_ITEMS.clear()
        calibration.FAVOURITE_ITEMS.update(saved_items)
    check("no special row, no Set price", driver.special_price(None) == (None, 0), str(driver.special_price(None)))
finally:
    calibration.load_shared = REAL_SHARED

typed = []
panel = {}
saved = (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
         m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
         m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
         calibration.steps_table, m.time.sleep, calibration.snap)
calibration.grab = lambda: object()
m.panel_holds_item = lambda image=None: False
calibration.slot_is_empty = lambda image, r, c: False
calibration.ctrl_click = lambda *a, **k: None
m.suggested_price = lambda verbose=False, listed_at=None, overlap=False: panel["market"]
calibration.click = lambda *a, **k: None
m.type_number = lambda value, clear: typed.append(value)
calibration.park = lambda *a, **k: None
m.panel_quantity = lambda want, verbose=False: panel["qty"]
m.check_price_field = lambda *a, **k: None
m.find_button = lambda *a, **k: (968, 634)
m.underprice_warning = lambda image=None: False
m.dialog_gone = lambda timeout=None: True
calibration.occupied_slots = lambda image=None: set()
calibration.steps_table = lambda *a, **k: None
m.time.sleep = lambda s: None
calibration.snap = lambda *a, **k: None


def listed(market, qty=1, **kw):
    typed.clear()
    panel.update(market=market, qty=qty)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        out = m.RowModel().seed({})._list_slot(1, 1, verbose=True, **kw)
    return out, said.getvalue()


SPECIAL = dict(floor=0, expect_item=CHAOS, special=True, resolve=False)
shared()
try:
    markets()
    out, said = listed(745_000, price=732_407, expect_market=740_000, **SPECIAL)
    check("the special row goes out at the Set's 732,407, 12,593 under the core's 745,000 market",
          out["price"] == 732_407 and typed[0] == 732_407, f"{out['price']:,}, typed {typed}")

    out, said = listed(720_000, price=732_407, expect_market=740_000, **SPECIAL)
    check("with the core cheaper than the Set, it still goes out at the Set's 732,407",
          out["price"] == 732_407 and typed[0] == 732_407, f"{out['price']:,}, typed {typed}")

    out, said = listed(98_874_945, price=732_407, expect_market=740_000, **SPECIAL)
    check("a 98,874,945 bundle loaded in the special row's place goes out at its own market, not 732,407",
          out["price"] == 98_874_944 and 732_407 not in typed, f"{out['price']:,}, typed {typed}")

    out, said = listed(745_000, price=732_407, expect_market=None, **SPECIAL)
    check("with no expected market handed in, what loaded is checked against the core's own 740,000: 732,407",
          out["price"] == 732_407, f"{out['price']:,}")

    out, said = listed(98_874_945, price=732_407, expect_market=None, **SPECIAL)
    check("and a bundle loaded with no expected market handed in still goes out at its own market",
          out["price"] == 98_874_944 and 732_407 not in typed, f"{out['price']:,}, typed {typed}")

    saved_anchor = m.market_anchor
    m.market_anchor = lambda name: 0
    try:
        out, said = listed(745_000, price=732_407, expect_market=None, **SPECIAL)
    finally:
        m.market_anchor = saved_anchor
    check("with no market known for one core at all, the Set price is not trusted over what loaded",
          out["price"] == 744_999, f"{out['price']:,}")

    out, said = listed(745_000, price=732_407, floor=0, expect_item=CHAOS, expect_market=740_000, resolve=False)
    check("a listing that is not the special row is still raised to its own market",
          out["price"] == 744_999, f"{out['price']:,}")

    out, said = listed(745_000, under=600, expect_market=740_000, **SPECIAL)
    check("with no Set price handed in, the special row goes out its amount under the market as before",
          out["price"] == 744_400, f"{out['price']:,}")

    markets()
    out, said = listed(98_874_945, floor=0, expect_item="Chaos Core Set X 135")
    check("relisting a Chaos Core Set X 135 at 98,874,945 is a check: 732,407 a Set",
          m.last_checked(SET) == 732_407 and driver.special_price(CHAOS) == (SET, 732_407),
          f"{m.last_checked(SET):,}")

    out, said = listed(39_553_163, floor=0, expect_item="Chaos Core Set X 135")
    check("a bundle that prices nothing like 135 Sets does not count: 292,986 a Set is ignored",
          m.last_checked(SET) == 732_407, f"{m.last_checked(SET):,}")

    out, said = listed(732_100, floor=0)
    check("a listing named only by its price does not count", m.last_checked(SET) == 732_407,
          f"{m.last_checked(SET):,}")

    out, said = listed(732_500 * 150, floor=0, expect_item=SET, unit_market=732_000, expect_market=732_000 * 150,
                       floor_each=723_067)
    check("listing freshly crafted Sets is a check: 150 in the bundle at 732,500 a Set",
          m.last_checked(SET) == 732_500, f"{m.last_checked(SET):,}")

    with contextlib.redirect_stdout(io.StringIO()):
        get_price._priced({"name": SET, "qty": 1, "pack": 1, "total": 733_100}, calibration.favourite_slot_of(SET),
                          SET, False)
    check("whichever check came last sets the price: the Purchase tab's 733,100",
          driver.special_price(CHAOS) == (SET, 733_100), str(driver.special_price(CHAOS)))
finally:
    calibration.load_shared = REAL_SHARED
    (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
     m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
     m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
     calibration.steps_table, m.time.sleep, calibration.snap) = saved


class Stop(Exception):
    pass


asked = []
screen = {}
saved = (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
         driver.select_after_withdrawal, calibration.grab, m.panel_holds_item, calibration.snap,
         calibration.steps_table, calibration.park)
driver.read_the_row = lambda model, index: (screen["text"], screen["row"], m.CHANGE_WORD, True)
m.refresh_table = lambda model=None, verbose=False: None
driver.reconcile_work_tab = lambda model, verbose=True: None
driver.tab_before_withdrawal = lambda model, verbose=True: None
driver.select_after_withdrawal = lambda: None
calibration.grab = lambda: object()
m.panel_holds_item = lambda image=None: False
calibration.snap = lambda *a, **k: None
calibration.steps_table = lambda *a, **k: None
calibration.park = lambda *a, **k: None


def list_back(*a, **kw):
    asked.append(kw)
    raise Stop()


def relist(text, row, change=None):
    asked.clear()
    shared(change)
    screen.update(text=text, row=row)
    model = m.RowModel().seed({})
    model._slots[3] = m.Row(row.name, qty=row.qty, price=row.price)
    model._seat = m.FIRST_SEAT
    model.scroll_to = lambda index, verbose=True: {"moved": False}
    model.next_work_slot = lambda: (1, 1)
    model.button = lambda image=None: m.CHANGE_WORD
    model.cancel = lambda index, **kw: None
    model.list_slot = list_back
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            driver.relist_one(model, 3, verbose=True, first=1, last=25)
        except Stop:
            pass
    return (asked[0] if asked else {}), said.getvalue()


UNDER = ON["resupply"]["special_row"]["under_set_market"][CHAOS]
SPREAD = ON["resupply"]["special_row"]["under_set_spread"]
drawn = []
real_randint = driver.random.randint
try:
    markets()
    m.note_checked(SET, 732_407)
    check("the Chaos special row goes 7,000 under the Set's market, give or take 200, from config",
          (UNDER, SPREAD) == (7_000, 200), f"{UNDER:,}, {SPREAD}")
    shared()
    prices = [driver.special_set_price(CHAOS, 732_407) for _ in range(4_000)]
    check(f"every draw lands in {732_407 - UNDER - SPREAD:,} to {732_407 - UNDER + SPREAD:,}, and both ends are "
          f"reached", min(prices) == 732_407 - UNDER - SPREAD and max(prices) == 732_407 - UNDER + SPREAD,
          f"{min(prices):,} to {max(prices):,}")
    driver.random.randint = lambda a, b: drawn.append((a, b)) or 137
    check("the random part is drawn from -200 to 200 and moves the price by that much",
          driver.special_set_price(CHAOS, 732_407) == 732_407 - UNDER - 137 and drawn[-1] == (-SPREAD, SPREAD),
          f"{drawn[-1:]}")
    check("with no Set price known there is no Set-based price", driver.special_set_price(CHAOS, 0) == 0, "")
    calibration.load_shared = REAL_SHARED

    core_row = ("Chaos Core 1 745,000 On Sale Change", m.Row(CHAOS, qty=1, price=745_000))
    kw, said = relist(*core_row)
    at = 732_407 - UNDER - 137
    check(f"the special row's relist asks for {at:,}: the Set's 732,407, {UNDER:,} under, 137 more drawn at "
          f"random",
          kw.get("price") == at and kw.get("under") is None and kw.get("special") is True, str(kw))
    check("its log says where the price comes from",
          f"row 3 is the Chaos Core special row; listing at {at:,}, {UNDER + 137:,} under the latest Chaos Core Set "
          f"market of 732,407, no floor" in said, said.strip().splitlines()[-1] if said.strip() else "")

    kw, said = relist(*core_row, change=lambda conf: conf.update(at_set_market={"default": False}))
    under = ON["resupply"]["special_row"]["under_market"]
    spread = ON["resupply"]["special_row"]["under_random_range"]
    check(f"with the Set price off, the relist goes {under:,} to {under + spread:,} under the market as before",
          kw.get("price") is None and under <= (kw.get("under") or 0) <= under + spread, str(kw))

    kw, said = relist("Chaos Core Set X 135 1 98,876,429 On Sale Change",
                      m.Row("Chaos Core Set X 135", qty=1, price=98_876_429))
    check("a Set row is not a special row and gets no special price",
          kw.get("price") is None and kw.get("special") is False, str(kw))
finally:
    driver.random.randint = real_randint
    calibration.load_shared = REAL_SHARED
    (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
     driver.select_after_withdrawal, calibration.grab, m.panel_holds_item, calibration.snap,
     calibration.steps_table, calibration.park) = saved

saved = (driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep)
driver.back_to_the_shop = lambda verbose=True: True
driver.register_tab = lambda verbose=True: None
calibration.click = lambda *a, **k: None
m.time.sleep = lambda s: None
try:
    shared()
    markets()
    m.note_checked(SET, 732_407)
    asked.clear()
    model = m.RowModel().seed({})
    model.release_work = lambda slot: None
    model.list_slot = lambda *a, **kw: asked.append(kw) or {"qty": 1, "price": kw.get("price") or 0, "units": 1}
    said = io.StringIO()
    driver.random.randint = lambda a, b: -88
    with contextlib.redirect_stdout(said):
        driver.special_list(model, (1, 1), CHAOS, 1, 25, verbose=False, cost=745_000)
    kw = asked[0] if asked else {}
    at = 732_407 - UNDER + 88
    check(f"the special row bought back goes out at {at:,}: the Set's 732,407, {UNDER:,} under, 88 back at "
          f"random",
          kw.get("price") == at and kw.get("under") is None and kw.get("special") is True, str(kw))
    check("and says so", f"the Chaos Core special row goes out at {at:,}, {UNDER - 88:,} under the latest Chaos "
          f"Core Set market of 732,407" in said.getvalue(), said.getvalue().strip())
finally:
    driver.random.randint = real_randint
    calibration.load_shared = REAL_SHARED
    driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep = saved

saved = (driver.shop_ready, driver.war.avoid, driver.alz_too_low)
driver.shop_ready = lambda *a, **k: False
driver.war.avoid = lambda *a, **k: None
driver.alz_too_low = lambda: False
try:
    shared()
    markets(core=731_000)
    m.note_checked(SET, 732_407)
    model = m.RowModel().seed({})
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        driver.resupply_special(model, 1, 30, verbose=True)
    check("the special row's header names the Set price",
          f"-- Chaos Core special row 1 of 1: {driver.special_qty()} at {UNDER - SPREAD:,} to {UNDER + SPREAD:,} "
          f"under the latest Chaos Core Set market of 732,407 --" in said.getvalue(),
          said.getvalue().strip().splitlines()[0] if said.getvalue().strip() else "")
finally:
    calibration.load_shared = REAL_SHARED
    driver.shop_ready, driver.war.avoid, driver.alz_too_low = saved

driver.holds_turn = held_turn
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
