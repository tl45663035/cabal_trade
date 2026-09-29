import ast
import contextlib
import copy
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_absolute_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import buy
import get_alz
import ledger
import row_model as m

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


REAL_SHARED = calibration.load_shared
RUN = calibration.load_shared()["run"]
CAP = RUN["never_buy_above"]["Chaos Core"]
SET_CAP = RUN["never_buy_above"]["Chaos Core Set"]
LOW = RUN["never_list_below"]["Chaos Core"]
SET_LOW = RUN["never_list_below"]["Chaos Core Set"]
SLOT = {name: int(s) for s, name in calibration.FAVOURITE_ITEMS.items()}
CONFIRM, CANCEL = (1000, 700), (1100, 700)

events = []
state = {}
saved = (buy.get_price.get_price, buy.show_work_tab, get_alz.read_balance, calibration.click, buy.await_dialog,
         buy.dialog_details, buy.dialog_button, buy.dialog_open, buy.await_balance, buy.time.sleep,
         calibration.park, calibration.snap, m.type_number, ledger.bought)
buy.get_price.get_price = lambda slot, verbose=True, search=True, **k: dict(state["offer"])
buy.show_work_tab = lambda: None
get_alz.read_balance = lambda *a, **k: state["after"]
calibration.click = lambda x, y, *a, **k: events.append(("click", (x, y)))
buy.await_dialog = lambda timeout=None: True
buy.dialog_details = lambda image=None: dict(state["detail"])
buy.dialog_button = lambda word, image=None: CONFIRM if word == buy.CONFIRM_WORD else CANCEL
buy.dialog_open = lambda image=None: False
buy.await_balance = lambda differs_from=None, timeout=None: state["after"]
buy.time.sleep = lambda s: None
calibration.park = lambda *a, **k: None
calibration.snap = lambda name, *a, **k: events.append(("snap", name))
m.type_number = lambda value, clear: events.append(("type", value))
ledger.bought = lambda *a, **k: events.append(("booked", a[0], a[1]))


def offer(name, each, pack=1, qty=5):
    return {"name": name, "qty": qty, "price": each * pack, "unit_price": each}


def buy_once(item, want, offered, detail, spend, balance=10 ** 12, special=False, shared=None):
    events.clear()
    state.update(offer=offered, detail=detail, after=balance - spend)
    calibration.load_shared = (lambda: copy.deepcopy(shared)) if shared else REAL_SHARED
    out = err = None
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            out = buy._buy_row_one(SLOT[item], want, balance=balance, special=special)
        except buy.Refused as exc:
            err = exc
    calibration.load_shared = REAL_SHARED
    clicks = [e[1] for e in events if e[0] == "click"]
    return out, err, clicks


try:
    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core", CAP + 1),
                                {"item": "Chaos Core", "price": CAP + 1, "qty": 1, "qty_max": 5}, CAP + 1)
    check(f"a Chaos Core row 1 at {CAP + 1:,} is refused before anything is clicked",
          err is not None and "never paid" in str(err) and clicks == [], f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core", CAP),
                                {"item": "Chaos Core", "price": CAP, "qty": 1, "qty_max": 5}, CAP)
    check(f"exactly {CAP:,} is bought", err is None and out["bought"] == 1 and CONFIRM in clicks,
          f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core", 720_000),
                                {"item": "Chaos Core", "price": 760_000, "qty": 1, "qty_max": 5}, 760_000)
    check("row 1 reads 720,000 but the dialog asks 760,000: cancelled on the dialog, Confirmation never pressed",
          err is not None and "never paid" in str(err) and CANCEL in clicks and CONFIRM not in clicks,
          f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core", 720_000),
                                {"item": "Chaos Core", "price": 0, "qty": 1, "qty_max": 5}, 720_000)
    check("a dialog price that does not read cancels the order rather than trusting row 1 alone",
          err is not None and "did not read" in str(err) and CONFIRM not in clicks, f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core Set", 3, offer("Chaos Core Set X 3", SET_CAP + 1, pack=3),
                                {"item": "Chaos Core Set", "price": 3 * (SET_CAP + 1), "qty": 1, "qty_max": 5},
                                3 * (SET_CAP + 1))
    check(f"a Chaos Core Set bundle at {SET_CAP + 1:,} a set is refused before anything is clicked",
          err is not None and "never paid" in str(err) and clicks == [], f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core Set", 3, offer("Chaos Core Set X 3", 730_000, pack=3, qty=2),
                                {"item": "Chaos Core Set", "price": 3 * 730_000, "qty": 1, "qty_max": 2},
                                3 * 730_000)
    check("a bundle of 3 at 730,000 a set is bought, its dialog price taken per set even with no X 3 in the name",
          err is None and out["bought"] == 3 and CONFIRM in clicks, f"{err}; {clicks}")

    out, err, clicks = buy_once("Upgrade Core (Ultimate)", 1, offer("Upgrade Core (Ultimate)", 900_000),
                                {"item": "Upgrade Core (Ultimate)", "price": 900_000, "qty": 1, "qty_max": 5},
                                900_000)
    check("an item with no limit is bought as before", err is None and CONFIRM in clicks, f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core", 800_000),
                                {"item": "Chaos Core", "price": 800_000, "qty": 1, "qty_max": 5}, 800_000,
                                special=True)
    check("the special row buys its one Chaos Core at 800,000, over the limit, on both reads",
          err is None and out["bought"] == 1 and CONFIRM in clicks, f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core", 2, offer("Chaos Core", 800_000),
                                {"item": "Chaos Core", "price": 800_000, "qty": 1, "qty_max": 5}, 800_000,
                                special=True)
    check("a special order for 2 is not the special row's order: refused",
          err is not None and "never paid" in str(err) and clicks == [], f"{err}; {clicks}")

    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core X 3", 800_000, pack=3),
                                {"item": "Chaos Core", "price": 2_400_000, "qty": 1, "qty_max": 5}, 2_400_000,
                                special=True)
    check("a bundle offered to the special row is not exempt: refused",
          err is not None and "never paid" in str(err) and clicks == [], f"{err}; {clicks}")

    off = REAL_SHARED()
    off["resupply"]["special_row"]["enabled"] = False
    out, err, clicks = buy_once("Chaos Core", 1, offer("Chaos Core", 800_000),
                                {"item": "Chaos Core", "price": 800_000, "qty": 1, "qty_max": 5}, 800_000,
                                special=True, shared=off)
    check("with special rows switched off, the special flag buys nothing over the limit",
          err is not None and "never paid" in str(err) and clicks == [], f"{err}; {clicks}")
finally:
    (buy.get_price.get_price, buy.show_work_tab, get_alz.read_balance, calibration.click, buy.await_dialog,
     buy.dialog_details, buy.dialog_button, buy.dialog_open, buy.await_balance, buy.time.sleep,
     calibration.park, calibration.snap, m.type_number, ledger.bought) = saved

typed = []
panel = {}
saved = (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
         m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
         m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
         calibration.steps_table, m.time.sleep, calibration.snap, calibration.load_shared)
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
real_shared = saved[-1]


def listed(market, qty=1, shared=None, **kw):
    typed.clear()
    panel.update(market=market, qty=qty)
    m.LAST_MARKET[m.item_key("Chaos Core")] = 700_000
    m.LAST_MARKET[m.item_key("Chaos Core Set")] = 700_000
    m.LAST_MARKET[m.item_key("Divine Stone")] = 660_000
    calibration.load_shared = (lambda: copy.deepcopy(shared)) if shared else real_shared
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        out = m.RowModel().seed({})._list_slot(1, 1, verbose=True, **kw)
    return out, typed[-1], said.getvalue()


try:
    out, last, said = listed(690_000 * 252, floor=0, expect_item="Chaos Core Set X 252")
    check(f"a relist of 252 sets with the market at 690,000 a set goes out at no less than {SET_LOW:,} x 252",
          out["price"] >= SET_LOW * 252 and last == out["price"], f"{out['price']:,}, typed {last:,}")

    lowered = real_shared()
    lowered["run"]["hard_min_per_unit"]["Chaos Core Set"] = 650_000
    out, last, said = listed(680_000 * 252, floor=0, expect_item="Chaos Core Set X 252", shared=lowered)
    check("with the old 650,000 set minimum back in config, the never-below rule still holds the sets at 710,000",
          out["price"] == SET_LOW * 252 and last == out["price"] and "never listed below" in said,
          f"{out['price']:,}")

    out, last, said = listed(700_000, floor=0, expect_item="Chaos Core")
    check(f"a single Chaos Core that is not a special row goes out at {LOW:,}, not the 699,999 undercut",
          out["price"] == LOW and last == LOW, f"{out['price']:,}")

    out, last, said = listed(700_000, qty=5, floor=0, expect_item="Chaos Core")
    check("a stack of 5 Chaos Cores goes out at 710,000 each", out["price"] == LOW, f"{out['price']:,}")

    special_price = calibration.undercut(700_000, 600)
    out, last, said = listed(700_000, floor=0, expect_item="Chaos Core", under=600, special=True, resolve=False)
    check(f"the special row, one Chaos Core, may go out under 710,000: {special_price:,}",
          out["price"] == special_price < LOW and "never listed below" not in said, f"{out['price']:,}")

    out, last, said = listed(700_000, qty=2, floor=0, expect_item="Chaos Core", under=600, special=True,
                             resolve=False)
    check("a special listing that turns out to hold 2 cores is not the special row: 710,000",
          out["price"] == LOW, f"{out['price']:,}")

    out, last, said = listed(690_000 * 3, floor=0, expect_item="Chaos Core Set X 3", under=600, special=True,
                             resolve=False)
    check("the special flag on a set bundle does not exempt it", out["price"] >= SET_LOW * 3, f"{out['price']:,}")

    no_special = real_shared()
    no_special["resupply"]["special_row"]["enabled"] = False
    out, last, said = listed(700_000, floor=0, expect_item="Chaos Core", under=600, special=True, resolve=False,
                             shared=no_special)
    check("with special rows switched off, nothing is exempt", out["price"] == LOW, f"{out['price']:,}")

    out, last, said = listed(690_000 * 252, rule_item="Chaos Core Set X 252")
    check("the supervisor's tab-4 listing that only names the item is held too",
          out["price"] >= SET_LOW * 252, f"{out['price']:,}")

    out, last, said = listed(690_000 * 150, expect_item="Chaos Core Set", unit_market=690_000,
                             expect_market=690_000 * 150, floor_each=690_000)
    check("freshly crafted sets, 150 in the bundle and no count in the name, go out at no less than 710,000 x 150",
          out["price"] >= SET_LOW * 150, f"{out['price']:,}")

    out, last, said = listed(650_000, floor=0, expect_item="Divine Stone", under=600, special=True, resolve=False)
    check("a Divine Stone special row is untouched by the Chaos rule",
          out["price"] == calibration.undercut(650_000, 600), f"{out['price']:,}")

    out, last, said = listed(705_000 * 3, floor=0, expect_item="Divine Stone Set X 3")
    check("Divine Stone Sets are not under the Chaos rule", out["price"] < SET_LOW * 3, f"{out['price']:,}")
finally:
    (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
     m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
     m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
     calibration.steps_table, m.time.sleep, calibration.snap, calibration.load_shared) = saved

class Stop(Exception):
    pass


asked = []
screen = {}
saved = (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
         driver.select_after_withdrawal, calibration.grab, m.panel_holds_item, calibration.snap,
         calibration.steps_table)
driver.read_the_row = lambda model, index: (screen["text"], screen["row"], m.CHANGE_WORD, True)
m.refresh_table = lambda model=None, verbose=False: None
driver.reconcile_work_tab = lambda model, verbose=True: None
driver.tab_before_withdrawal = lambda model, verbose=True: None
driver.select_after_withdrawal = lambda: None
calibration.grab = lambda: object()
m.panel_holds_item = lambda image=None: False
calibration.snap = lambda *a, **k: None
calibration.steps_table = lambda *a, **k: None


def list_back(*a, **kw):
    asked.append(kw)
    raise Stop()


try:
    for text, row, want in (("Chaos Core 1 700,000 On Sale Change", m.Row("Chaos Core", qty=1, price=700_000), True),
                            ("Chaos Core Set X 3 1 2,100,000 On Sale Change",
                             m.Row("Chaos Core Set X 3", qty=1, price=2_100_000), False)):
        asked.clear()
        screen.update(text=text, row=row)
        model = m.RowModel().seed({})
        model._slots[3] = m.Row(row.name, qty=row.qty, price=row.price)
        model._seat = m.FIRST_SEAT
        model.scroll_to = lambda index, verbose=True: {"moved": False}
        model.next_work_slot = lambda: (1, 1)
        model.button = lambda image=None: m.CHANGE_WORD
        model.cancel = lambda index, **kw: None
        model.list_slot = list_back
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                driver.relist_one(model, 3, verbose=False, first=1, last=25)
            except Stop:
                pass
        check(f"the relist of {row.name!r} asks for the special exemption: {want}",
              asked and asked[0].get("special") is want, str(asked[0].get("special") if asked else asked))
finally:
    (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
     driver.select_after_withdrawal, calibration.grab, m.panel_holds_item, calibration.snap,
     calibration.steps_table) = saved

class Finder(ast.NodeVisitor):
    def __init__(self):
        self.stack, self.found = [], []

    def visit_FunctionDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node):
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if (name in ("list_slot", "_list_slot", "dict", "buy_row_one", "_buy_row_one")
                and any(k.arg == "special" for k in node.keywords)):
            self.found.append((self.stack[0] if self.stack else "", name))
        self.generic_visit(node)

    def visit_Dict(self, node):
        for key, value in zip(node.keys, node.values):
            if (isinstance(key, ast.Constant) and key.value == "kind"
                    and isinstance(value, ast.Constant) and value.value == "special"):
                self.found.append((self.stack[0] if self.stack else "", "special job"))
        self.generic_visit(node)


passing = []
for path in (SRC / "driver.py", SRC / "row_model.py", SRC / "buy.py", SRC / "craft.py", SRC / "convert.py",
             SRC.parent / "tools" / "supervise.py"):
    finder = Finder()
    finder.visit(ast.parse(path.read_text(encoding="utf-8")))
    passing += [(path.name, *f) for f in finder.found]
check("only the special row asks for the exemption: its relist, its listing, and the order its own job places",
      sorted(passing) == [("buy.py", "buy_row_one", "_buy_row_one"), ("driver.py", "relist_one", "dict"),
                          ("driver.py", "resupply_special", "special job"),
                          ("driver.py", "special_list", "list_slot"),
                          ("driver.py", "take_offers", "buy_row_one")],
      str(passing))

placed = []
saved = buy.buy_row_one
buy.buy_row_one = lambda *a, **k: placed.append(k.get("special")) or {
    "bought": 1, "spent": 800_000, "balance": 1, "balance_seen": True, "packs": 1, "unit_price": 800_000,
    "price": 800_000}
try:
    for kind, want in (("special", True), ("resupply", False)):
        placed.clear()
        job = {"kind": kind, "core": "Chaos Core", "slot": SLOT["Chaos Core"], "target": 1, "want_max": None,
               "sells_at": 0, "gap": None, "leave": 1, "steps_max": 1, "take_all": 1, "orders": 0, "bought": 0,
               "paid": 0}
        with contextlib.redirect_stdout(io.StringIO()):
            driver.take_offers(job, 1, 1, on_margin=False, verbose=False)
        check(f"a {kind} job's order asks for the exemption: {want}", placed == [want], str(placed))
finally:
    buy.buy_row_one = saved

calls = []
saved = driver.do_list
driver.do_list = lambda *a, **k: calls.append((a, k))
try:
    with contextlib.redirect_stdout(io.StringIO()):
        driver._dispatch(["list-named", "1", "1", "0", "4", "Chaos Core Set X 252"])
        driver._dispatch(["list-named", "1", "2", "0", "4", "Chaos", "Core"])
    check("list-named hands the name to the listing and no price or floor of its own",
          calls[0] == ((1, 1, None), {"tab": 4, "named": "Chaos Core Set X 252"})
          and calls[1][1]["named"] == "Chaos Core", str(calls))
finally:
    driver.do_list = saved

spec = importlib.util.spec_from_file_location("supervise", SRC.parent / "tools" / "supervise.py")
sup = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(sup)
sent = []
rows = []
saved = (sup.first_row, sup.run_driver, sup.event)
sup.first_row = lambda tab: rows.pop(0) if rows else []
sup.run_driver = lambda *args: sent.append(args)
sup.event = lambda *a, **k: None
try:
    for tasks, want in ((([("resupply", {"core": "Chaos Core", "special": True, "qty": 1})]),
                         ("list-named", "1", "1", "0", str(sup.WORK_TAB), "Chaos Core")),
                        ((), ("list", "1", "1", "0", str(sup.WORK_TAB)))):
        sent.clear()
        rows[:] = [[(1, 1)], [], []]
        with contextlib.redirect_stdout(io.StringIO()):
            sup.clear_work_tab(8, tasks)
        listing = [a for a in sent if a[0].startswith("list")]
        check(f"the supervisor's tab-4 listing {'names' if len(want) > 5 else 'cannot name'} what the log says: {want[0]}",
              listing == [want], str(sent))
finally:
    sup.first_row, sup.run_driver, sup.event = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
