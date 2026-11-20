import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_leave_behind_")) / "sales.db")
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


CHAOS, SET, DIVINE, HIGH = "Chaos Core", "Chaos Core Set", "Divine Stone", "Force Core(High)"
SLOT = {name: calibration.favourite_slot_of(name) for name in (CHAOS, HIGH)}
REAL_SHARED = calibration.load_shared
ON = copy.deepcopy(REAL_SHARED())
ON["resupply"]["special_row"]["enabled"] = True
LOW, TOP = calibration.leave_behind_range(CHAOS)
UNDER = ON["resupply"]["special_row"]["under_market"]
SPREAD = ON["resupply"]["special_row"]["under_random_range"]
# The live config has no listing minimum for Chaos Core since 2026-11-19; the checks of the minimum set their own.
MIN_LIST = 500_000
FLOORED = REAL_SHARED()
FLOORED["run"]["never_list_below"][CHAOS] = MIN_LIST
real_randint = driver.random.randint
held_turn = driver.holds_turn
driver.holds_turn = lambda: True


def ranged(low, high):
    conf = copy.deepcopy(ON)
    conf["resupply"]["leave_behind"].update(min=low, max=high)
    return conf


check("one setting: Chaos Core and Divine Stone both read 5 to 15",
      (LOW, TOP) == (5, 15) and calibration.leave_behind_range(DIVINE) == (5, 15), f"{LOW} to {TOP}")
check("no other item has the rule",
      calibration.leave_behind_range(HIGH) == (0, 0) and calibration.leave_behind_range(SET) == (0, 0), "")
check("rows of the range's top or fewer are never bought, for both items",
      calibration.buy_skip_rows_up_to(CHAOS) == TOP and calibration.buy_skip_rows_up_to(DIVINE) == TOP, "")
check("and no other item passes over any row",
      calibration.buy_skip_rows_up_to(HIGH) == 0 and calibration.buy_skip_rows_up_to(SET) == 0, "")
configs = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted((SRC / "configs").glob("*.json"))}
check("no machine's config changes the range, so every machine uses the same one",
      all(c.get("resupply", {}).get("leave_behind") is None for c in configs.values()), str(list(configs)))
check("no machine's config changes the special row's price either",
      all("under_market" not in c.get("resupply", {}).get("special_row", {}) for c in configs.values()), "")
replaced = ([k for k in ("buy_leave_behind", "buy_skip_rows_up_to", "buy_take_all_after") if k in ON["resupply"]]
            + [k for k in ("qty", "at_set_market", "under_set_market", "under_set_spread")
               if k in ON["resupply"]["special_row"]])
check("the settings the range replaces are gone", not replaced, str(replaced))
check("the special row goes 2,000 under the market, give or take 500, from config", (UNDER, SPREAD) == (2_000, 500),
      f"{UNDER:,}, {SPREAD}")

calibration.load_shared = lambda: copy.deepcopy(ranged(3, 8))
try:
    rolls = {driver.roll_leave_behind(CHAOS) for _ in range(2_000)}
    sizes = {driver.special_qty(CHAOS) for _ in range(2_000)}
    check("changing the range to 3-8 moves what is left behind", rolls == set(range(3, 9)), str(sorted(rolls)))
    check("and which rows are passed over", calibration.buy_skip_rows_up_to(DIVINE) == 8, "")
    check("and how many the special row buys", sizes == set(range(3, 9)), str(sorted(sizes)))
    check("and the largest order that counts as the special row's own",
          m.special_single(CHAOS, 8) and not m.special_single(CHAOS, 9), "")
finally:
    calibration.load_shared = REAL_SHARED

for low, high in ((9, 3), (-1, 5), ("5", 15), (5, None)):
    try:
        calibration._check_leave_behind({"resupply": {"leave_behind": {"min": low, "max": high}}})
        check(f"a range of {low!r} to {high!r} stops the run before it starts", False)
    except SystemExit as exc:
        check(f"a range of {low!r} to {high!r} stops the run before it starts", "leave_behind" in str(exc), str(exc))

rolls = [driver.roll_leave_behind(CHAOS) for _ in range(4_000)]
check(f"every leave-behind roll is a whole number from {LOW} to {TOP}, and each value comes up",
      set(rolls) == set(range(LOW, TOP + 1)), str(sorted(set(rolls))))
sizes = [driver.special_qty(CHAOS) for _ in range(4_000)]
check(f"every special row size is a whole number from {LOW} to {TOP}, and each value comes up",
      set(sizes) == set(range(LOW, TOP + 1)), str(sorted(set(sizes))))
check("an item outside the rule leaves nothing behind", driver.roll_leave_behind(HIGH) == 0, "")
said = io.StringIO()
with contextlib.redirect_stdout(said):
    driver.say_leave_behind(HIGH, 30)
check("and says nothing about it", said.getvalue() == "", said.getvalue())

CONFIRM, CANCEL = (1000, 700), (1100, 700)
events, state, offers = [], {}, []
saved = (buy.get_price.get_price, buy.show_work_tab, get_alz.read_balance, calibration.click, buy.await_dialog,
         buy.dialog_details, buy.dialog_button, buy.dialog_open, buy.await_balance, buy.time.sleep,
         calibration.park, calibration.snap, m.type_number, ledger.bought, buy.scroll_down, calibration.load_shared)


def read_offer(slot, verbose=True, search=True, **k):
    offer = offers.pop(0)
    events.append(("read", offer["qty"]))
    return dict(offer)


buy.get_price.get_price = read_offer
buy.show_work_tab = lambda: None
get_alz.read_balance = lambda *a, **k: state["balance"]
calibration.click = lambda x, y, *a, **k: events.append(("click", (x, y)))
buy.await_dialog = lambda timeout=None: True
buy.dialog_details = lambda image=None: dict(state["detail"])
buy.dialog_button = lambda word, image=None: CONFIRM if word == buy.CONFIRM_WORD else CANCEL
buy.dialog_open = lambda image=None: False
buy.await_balance = lambda differs_from=None, timeout=None: state["balance"] - state["spend"]
buy.time.sleep = lambda s: None
calibration.park = lambda *a, **k: None
calibration.snap = lambda name, *a, **k: None
m.type_number = lambda value, clear: events.append(("type", value))
ledger.bought = lambda *a, **k: events.append(("booked", a[0], a[1], a[3]))
buy.scroll_down = lambda notches=1, verbose=False: events.append(("scroll", notches))


def take(rows, want=10, item=CHAOS, kind=None, roll=LOW, shared=None):
    events.clear()
    offers[:] = [{"name": item, "qty": q, "price": p, "unit_price": p} for q, p in rows]
    leave = roll if calibration.leave_behind_range(item)[1] else 0
    last_q, last_p = rows[-1]
    state.update(balance=10 ** 12, spend=min(want, last_q - leave) * last_p,
                 detail={"item": item, "price": last_p, "qty": 1, "qty_max": last_q})
    job = {"core": item, "slot": SLOT[item], "target": want, "want_max": None, "sells_at": 0, "gap": None,
           "steps_max": len(rows) - 1, "orders": 0, "bought": 0, "paid": 0, "balance": 10 ** 12,
           **({"kind": kind} if kind else {})}
    driver.random.randint = lambda a, b: roll
    calibration.load_shared = (lambda: copy.deepcopy(shared)) if shared else REAL_SHARED
    said = io.StringIO()
    try:
        with contextlib.redirect_stdout(said):
            ok = driver.take_offers(job, want, 1, on_margin=False, verbose=True)
    finally:
        calibration.load_shared = REAL_SHARED
        driver.random.randint = real_randint
    return ok, job, said.getvalue()


def clicked_before_scroll():
    first = next((i for i, e in enumerate(events) if e[0] == "scroll"), len(events))
    return [e for e in events[:first] if e[0] == "click"]


saved_items = dict(calibration.FAVOURITE_ITEMS)
try:
    ok, job, said = take([(TOP, 700_000), (50, 731_000)], roll=7)
    check(f"a Chaos Core row of {TOP} is passed over, nothing clicked, and the next row of 50 gives the 10",
          ok and job["bought"] == 10 and job["paid"] == 7_310_000 and clicked_before_scroll() == []
          and events.count(("scroll", 1)) == 1, f"{job['bought']} for {job['paid']:,}; {events[:4]}")
    check("the log says why it moved on", f"row 1 holds {TOP}, and a Chaos Core row of {TOP} or fewer is never bought; "
          f"moving on to the next row" in said, "")

    ok, job, said = take([(LOW - 1, 690_000), (LOW, 695_000), (TOP, 700_000), (TOP + 9, 731_000)], want=50, roll=TOP)
    check(f"rows of {LOW - 1}, {LOW} and {TOP} are passed over, and a row of {TOP + 9} with {TOP} left behind gives 9",
          ok and job["bought"] == 9 and ("type", 9) in events and events.count(("scroll", 1)) == 3,
          f"{job['bought']}; {events}")

    ok, job, said = take([(30, 731_000)], want=50, roll=LOW)
    check(f"a row of 30 with {LOW} left behind gives {30 - LOW}", ok and job["bought"] == 30 - LOW
          and ("type", 30 - LOW) in events, f"{job['bought']}; {events}")
    check("the log says how many stays behind", f"row 1 holds 30, {LOW} stays behind, so at most {30 - LOW} comes off "
          f"it" in said, said)

    ok, job, said = take([(TOP + 1, 731_000)], want=1, roll=TOP)
    check(f"the thinnest row bought, {TOP + 1} with the most left behind, still has one to spare",
          ok and job["bought"] == 1, f"{job['bought']}; {events}")

    calibration.FAVOURITE_ITEMS.update({"11": DIVINE, "12": "Divine Stone Set"})
    SLOT[DIVINE] = 11
    ok, job, said = take([(TOP, 650_000), (40, 655_000)], want=100, item=DIVINE, roll=11)
    check(f"Divine Stone follows the same rules: its row of {TOP} is passed over, and 11 of the next 40 stay behind",
          ok and job["bought"] == 29 and clicked_before_scroll() == [], f"{job['bought']}; {events[:4]}")
    calibration.FAVOURITE_ITEMS.clear()
    calibration.FAVOURITE_ITEMS.update(saved_items)

    thin = [(TOP - i % 3, 700_000 + i) for i in range(12)]
    ok, job, said = take(thin + [(40, 731_000)], want=100, roll=LOW + 2)
    check("twelve thin rows down the order still leaves its roll behind, and never takes the row whole",
          ok and job["bought"] == 40 - (LOW + 2) and ("type", 40 - (LOW + 2)) in events
          and "takes the row it is on whole" not in said, f"{job['bought']}; {events[-6:]}")

    ok, job, said = take([(TOP, 700_000), (50, 731_000)], kind="special", shared=ON)
    check("the special row's own buy passes over a thin row too, so it never buys another machine's special row",
          ok and job["bought"] == 10 and clicked_before_scroll() == [], f"{job['bought']}; {events[:4]}")

    ok, job, said = take([(TOP, 700_000), (LOW, 702_000)])
    check(f"with nothing but rows of {TOP} or fewer down the steps, nothing is bought",
          not ok and job["bought"] == 0 and not any(e[0] == "click" for e in events), str(events))

    ok, job, said = take([(5, 250_000)], want=5, item=HIGH)
    check("a Force Core(High) row of 5 is bought whole, as before", ok and job["bought"] == 5 and ("type", 5) in events,
          f"{job['bought']}; {events}")

    events.clear()
    offers[:] = [{"name": CHAOS, "qty": 50, "price": 800_000, "unit_price": 800_000}]
    state.update(balance=10 ** 12, spend=TOP * 800_000, detail={"item": CHAOS, "price": 800_000, "qty": 1, "qty_max": 50})
    calibration.load_shared = lambda: copy.deepcopy(ON)
    with contextlib.redirect_stdout(io.StringIO()):
        out = buy._buy_row_one(SLOT[CHAOS], TOP, balance=10 ** 12, special=True, leave_behind=LOW)
    check(f"the special row's order for {TOP} still buys over the 750,000 limit",
          out["bought"] == TOP and CONFIRM in [e[1] for e in events if e[0] == "click"], str(out))
    offers[:] = [{"name": CHAOS, "qty": 50, "price": 800_000, "unit_price": 800_000}]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            buy._buy_row_one(SLOT[CHAOS], TOP + 1, balance=10 ** 12, special=True, leave_behind=LOW)
        refused = ""
    except buy.Refused as exc:
        refused = str(exc)
    check(f"an order for {TOP + 1} is too big to be the special row's, so the limit holds", "never paid" in refused,
          refused)
    check("orders of 1 up to the range's top count as the special row's own, and nothing else does",
          all(m.special_single(CHAOS, q) for q in range(1, TOP + 1)) and not m.special_single(CHAOS, TOP + 1)
          and not m.special_single(CHAOS, 0) and not m.special_single("Chaos Core Set X 3", 3), "")
    calibration.load_shared = REAL_SHARED
    check("with special rows off, no order counts as the special row's", not m.special_single(CHAOS, LOW), "")
finally:
    driver.random.randint = real_randint
    calibration.FAVOURITE_ITEMS.clear()
    calibration.FAVOURITE_ITEMS.update(saved_items)
    (buy.get_price.get_price, buy.show_work_tab, get_alz.read_balance, calibration.click, buy.await_dialog,
     buy.dialog_details, buy.dialog_button, buy.dialog_open, buy.await_balance, buy.time.sleep,
     calibration.park, calibration.snap, m.type_number, ledger.bought, buy.scroll_down,
     calibration.load_shared) = saved

seen = []
rolls = iter([6, 13, 9])
saved = buy.buy_row_one
buy.buy_row_one = lambda slot, want, **k: seen.append(k["leave_behind"]) or {
    "bought": 1, "spent": 731_000, "balance": 1, "balance_seen": True, "packs": 1, "unit_price": 731_000,
    "price": 731_000}
driver.random.randint = lambda a, b: next(rolls)
try:
    job = {"core": CHAOS, "slot": SLOT[CHAOS], "target": 3, "want_max": None, "sells_at": 0, "gap": None,
           "steps_max": 1, "orders": 0, "bought": 0, "paid": 0}
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(3):
            driver.take_offers(job, 1, 1, on_margin=False, verbose=False)
    check("every order rolls its own number to leave behind", seen == [6, 13, 9], str(seen))
finally:
    buy.buy_row_one = saved
    driver.random.randint = real_randint

orders, script, placed = [], [], []


def fake_offers(job, want, batch, on_margin=True, verbose=True):
    orders.append(want)
    got = script.pop(0) if script else 0
    if got <= 0:
        return False
    job["bought"] += got
    job["paid"] += got * 731_000
    return True


def rolled_again(a, b):
    raise AssertionError("the special row rolled its size a second time")


def special_buy(target, fills):
    orders.clear()
    placed.clear()
    script[:] = list(fills)
    job = {"kind": "special", "core": CHAOS, "slot": SLOT[CHAOS], "landing": (1, 1), "step": "buy", "bought": 0,
           "paid": 0, "listed": 0, "rows": [], "ordinal": 0, "target": target, "want_max": None, "sells_at": 0,
           "gap": None, "steps_max": 1, "orders": 0}
    model = m.RowModel().seed({})
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            out = driver.finish_special(model, job, 1, 25, verbose=False)
        except buy.Refused as exc:
            out = exc
    return out, job


saved = (driver.take_offers, driver.work_tab_slots, driver.await_arrival, driver.special_list, calibration.load_shared)
driver.take_offers = fake_offers
driver.work_tab_slots = lambda verbose=True: set()
driver.await_arrival = lambda before, verbose=True: {(1, 1)}
driver.special_list = lambda model, landing, core, first, last, verbose=True, cost=0, ordinal=0: placed.append(
    (core, cost)) or 5
calibration.load_shared = lambda: copy.deepcopy(ON)
driver.random.randint = rolled_again
try:
    out, job = special_buy(12, [12])
    check("the special row buys the number it rolled, in one order, and lists them",
          orders == [12] and job["bought"] == 12 and placed == [(CHAOS, 731_000)] and out["bought"] == 12,
          f"orders {orders}, listed {placed}")

    out, job = special_buy(12, [7, 5])
    check("an order that gives only 7 is topped up for the 5 short, keeping the number it rolled",
          orders == [12, 5] and job["bought"] == 12 and placed, f"orders {orders}")

    out, job = special_buy(12, [7, 0])
    check("if the top-up buys nothing, the 7 bought are still listed", orders == [12, 5] and job["bought"] == 7
          and placed and not isinstance(out, Exception), f"orders {orders}, {out}")

    out, job = special_buy(12, [0])
    check("nothing bought: nothing listed", isinstance(out, buy.Refused) and not placed, str(out))
finally:
    driver.random.randint = real_randint
    (driver.take_offers, driver.work_tab_slots, driver.await_arrival, driver.special_list,
     calibration.load_shared) = saved

saved = (driver.shop_ready, driver.war.avoid, driver.alz_too_low, driver.get_alz.read_balance)
driver.shop_ready = lambda *a, **k: False
driver.war.avoid = lambda *a, **k: None
driver.alz_too_low = lambda: False
driver.get_alz.read_balance = lambda *a, **k: 900_000_000
calibration.load_shared = lambda: copy.deepcopy(ON)
driver.random.randint = lambda a, b: 11
said = io.StringIO()
try:
    with contextlib.redirect_stdout(said):
        driver.resupply_special(m.RowModel().seed({}), 1, 30, verbose=True)
finally:
    driver.random.randint = real_randint
    calibration.load_shared = REAL_SHARED
    driver.shop_ready, driver.war.avoid, driver.alz_too_low, driver.get_alz.read_balance = saved
check("the header says how many it rolled and how far under the market it goes",
      f"-- Chaos Core special row 1 of 1: 11 at {UNDER - SPREAD:,} to {UNDER + SPREAD:,} under the market --"
      in said.getvalue(), said.getvalue().strip())
check("and the log says what stays behind and which rows are passed over",
      f"{LOW} to {TOP} stays behind on every row bought" in said.getvalue()
      and f"a row of {TOP} or fewer is passed over" in said.getvalue(), said.getvalue().strip())

calibration.load_shared = lambda: copy.deepcopy(ON)
try:
    for core in (CHAOS, DIVINE):
        cuts = [driver.special_undercut(core) for _ in range(4_000)]
        drawn = []
        driver.random.randint = lambda a, b: drawn.append((a, b)) or a
        lowest = driver.special_undercut(core)
        driver.random.randint = lambda a, b: b
        highest = driver.special_undercut(core)
        driver.random.randint = real_randint
        check(f"a {core} special row goes {UNDER - SPREAD:,} to {UNDER + SPREAD:,} under its market, and no further",
              min(cuts) >= UNDER - SPREAD and max(cuts) <= UNDER + SPREAD and len(set(cuts)) > 100
              and (lowest, highest) == (UNDER - SPREAD, UNDER + SPREAD) and drawn == [(-SPREAD, SPREAD)],
              f"{min(cuts):,} to {max(cuts):,}; ends {lowest:,} and {highest:,}")
finally:
    driver.random.randint = real_randint
    calibration.load_shared = REAL_SHARED

asked = []
saved = (driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep)
driver.back_to_the_shop = lambda verbose=True: True
driver.register_tab = lambda verbose=True: None
calibration.click = lambda *a, **k: None
m.time.sleep = lambda s: None
calibration.load_shared = lambda: copy.deepcopy(ON)
driver.random.randint = lambda a, b: -137
try:
    model = m.RowModel().seed({})
    model.release_work = lambda slot: None
    model.list_slot = lambda *a, **kw: asked.append(kw) or {"qty": 12, "price": 519_137, "units": 12}
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        lands_in = driver.special_list(model, (1, 1), CHAOS, 1, 25, verbose=False, cost=526_000)
    kw = asked[0] if asked else {}
    check(f"the listing goes {UNDER - 137:,} under the core's own market, with no fixed price and no floor",
          kw.get("under") == UNDER - 137 and kw.get("price") is None and kw.get("floor") == 0, str(kw))
    check("and says so", f"the Chaos Core special row goes out {UNDER - 137:,} under the market" in said.getvalue(),
          said.getvalue().strip())
    row = model._slots.get(lands_in)
    check("the row of 12 is known as the special row", row is not None and row.qty == 12
          and driver.special_of(row) == CHAOS, str(row))
finally:
    driver.random.randint = real_randint
    calibration.load_shared = REAL_SHARED
    driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep = saved


class Stop(Exception):
    pass


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


def relist(text, row):
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
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            driver.relist_one(model, 3, verbose=True, first=1, last=25)
        except Stop:
            pass
    return (asked[0] if asked else {}), said.getvalue()


calibration.load_shared = lambda: copy.deepcopy(ON)
driver.random.randint = lambda a, b: 137
try:
    kw, said = relist("Chaos Core 12 519,000 On Sale Change", m.Row(CHAOS, qty=12, price=519_000))
    check(f"the special row's relist goes {UNDER + 137:,} under the core's own market, with no fixed price",
          kw.get("under") == UNDER + 137 and kw.get("price") is None and kw.get("listed_at") is None, str(kw))
    check("and its log says so",
          f"row 3 is the Chaos Core special row; listing {UNDER + 137:,} under the market, no floor" in said, said)
    kw, said = relist("Chaos Core Set X 135 1 98,876,429 On Sale Change",
                      m.Row("Chaos Core Set X 135", qty=1, price=98_876_429))
    check("a Set row is not a special row and gets no special price",
          kw.get("under") is None and kw.get("price") is None, str(kw))
finally:
    driver.random.randint = real_randint
    calibration.load_shared = REAL_SHARED
    (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
     driver.select_after_withdrawal, calibration.grab, m.panel_holds_item, calibration.snap,
     calibration.steps_table, calibration.park) = saved

typed, panel = [], {}
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


def listed(market, item=CHAOS, shared=None):
    typed.clear()
    panel.update(market=market, qty=12)
    said = io.StringIO()
    calibration.load_shared = (lambda: copy.deepcopy(shared)) if shared else REAL_SHARED
    try:
        with contextlib.redirect_stdout(said):
            out = m.RowModel().seed({})._list_slot(1, 1, verbose=True, floor=0, expect_item=item, under=UNDER,
                                                   expect_market=market, resolve=False)
    finally:
        calibration.load_shared = REAL_SHARED
    return out, said.getvalue()


try:
    out, said = listed(526_000)
    check(f"with the core at 526,000 the special row is typed at {526_000 - UNDER:,}",
          out["price"] == 526_000 - UNDER and typed[0] == 526_000 - UNDER, f"{out['price']:,}, typed {typed}")
    out, said = listed(400_000)
    check(f"with no minimum in the live config, a stray 400,000 row takes the special row to {400_000 - UNDER:,}",
          out["price"] == 400_000 - UNDER and "never listed below" not in said, f"{out['price']:,}, typed {typed}")
    out, said = listed(400_000, shared=FLOORED)
    check(f"a stray 400,000 row cannot drag the special row under the {MIN_LIST:,} minimum",
          out["price"] == MIN_LIST and typed[-1] != 400_000 - UNDER and "never listed below" in said,
          f"{out['price']:,}, typed {typed}")
    out, said = listed(MIN_LIST + UNDER - 1, shared=FLOORED)
    check(f"a price one Alz under the minimum is raised to {MIN_LIST:,}", out["price"] == MIN_LIST, f"{out['price']:,}")
    out, said = listed(MIN_LIST + UNDER, shared=FLOORED)
    check(f"a price exactly on the minimum stays at {MIN_LIST:,}",
          out["price"] == MIN_LIST and "never listed below" not in said, f"{out['price']:,}")
    out, said = listed(400_000, item=DIVINE)
    check("Divine Stone has no minimum configured, so it follows its market down",
          out["price"] == 400_000 - UNDER, f"{out['price']:,}")
finally:
    (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
     m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
     m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
     calibration.steps_table, m.time.sleep, calibration.snap) = saved

spec = importlib.util.spec_from_file_location("supervise", SRC.parent / "tools" / "supervise.py")
sup = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(sup)
CRAFT = ("craft", str(SLOT[CHAOS]))
calibration.load_shared = lambda: copy.deepcopy(ON)
try:
    check("the supervisor reads the special row's size from the same range", sup.special_qty_for(CHAOS) == (LOW, TOP),
          str(sup.special_qty_for(CHAOS)))
    check("a dead run holding a stack the special row could have bought does not craft it",
          all(sup.step_for("holding", CHAOS, qty) is None for qty in (LOW, LOW + 3, TOP)), "")
    check("a stack bigger than the range is crafted", sup.step_for("holding", CHAOS, TOP + 3) == CRAFT,
          str(sup.step_for("holding", CHAOS, TOP + 3)))
    check("a stack smaller than the range is crafted", sup.step_for("holding", CHAOS, LOW - 1) == CRAFT,
          str(sup.step_for("holding", CHAOS, LOW - 1)))
finally:
    calibration.load_shared = REAL_SHARED
check("with special rows off, every stack is crafted",
      sup.special_qty_for(CHAOS) is None and sup.step_for("holding", CHAOS, LOW + 3) == CRAFT, "")

model = m.RowModel().seed({})
check("with special rows off, no special row is wanted, so nothing is bought for one",
      driver.special_wanted(model, 1, 30) == [], str(driver.special_wanted(model, 1, 30)))

driver.holds_turn = held_turn
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
