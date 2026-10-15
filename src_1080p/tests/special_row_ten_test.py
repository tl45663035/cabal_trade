import contextlib
import copy
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_special_row_ten_")) / "sales.db")
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


CHAOS, SET, HIGH = "Chaos Core", "Chaos Core Set", "Force Core(High)"
SLOT = {name: calibration.favourite_slot_of(name) for name in (CHAOS, HIGH)}
REAL_SHARED = calibration.load_shared
ON = copy.deepcopy(REAL_SHARED())
ON["resupply"]["special_row"]["enabled"] = True
held_turn = driver.holds_turn
driver.holds_turn = lambda: True
SKIP = calibration.buy_skip_rows_up_to(CHAOS)

check("a Chaos Core row of 10 or fewer is never bought: 10 from config", SKIP == 10, str(SKIP))
check("no other item has the rule", calibration.buy_skip_rows_up_to(HIGH) == 0
      and calibration.buy_skip_rows_up_to("Divine Stone") == 0 and calibration.buy_skip_rows_up_to(SET) == 0, "")
overrides = {p.name: json.loads(p.read_text(encoding="utf-8")).get("resupply", {}).get("buy_skip_rows_up_to")
             for p in sorted((SRC / "configs").glob("*.json"))}
check("no machine's config changes it, so every machine stops at 10", all(v is None for v in overrides.values()),
      str(overrides))
check("the rule does not depend on special rows: they are off on bot and it still reads 10",
      not REAL_SHARED()["resupply"]["special_row"]["enabled"] and SKIP == 10, "")
check("the special row buys 10", ON["resupply"]["special_row"]["qty"] == 10, str(ON["resupply"]["special_row"]["qty"]))

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


def take(rows, want=10, item=CHAOS, kind=None, take_all=10, shared=None):
    events.clear()
    offers[:] = [{"name": item, "qty": q, "price": p, "unit_price": p} for q, p in rows]
    leave = int(calibration.buy_leave_behind(item))
    last_q, last_p = rows[-1]
    asked = min(want, last_q - leave if take_all else last_q)
    state.update(balance=10 ** 12, spend=asked * last_p,
                 detail={"item": item, "price": last_p, "qty": 1, "qty_max": last_q})
    job = {"core": item, "slot": SLOT[item], "target": want, "want_max": None, "sells_at": 0, "gap": None,
           "leave": leave, "steps_max": len(rows) - 1, "take_all": take_all, "orders": 0, "bought": 0, "paid": 0,
           "balance": 10 ** 12, **({"kind": kind} if kind else {})}
    calibration.load_shared = (lambda: copy.deepcopy(shared)) if shared else REAL_SHARED
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        ok = driver.take_offers(job, want, 1, on_margin=False, verbose=True)
    calibration.load_shared = REAL_SHARED
    return ok, job, said.getvalue()


def clicked_before_scroll():
    first = next((i for i, e in enumerate(events) if e[0] == "scroll"), len(events))
    return [e for e in events[:first] if e[0] == "click"]


try:
    ok, job, said = take([(10, 700_000), (50, 731_000)])
    check("a Chaos Core row of 10 is passed over, nothing clicked, and the next row of 50 gives the 10",
          ok and job["bought"] == 10 and job["paid"] == 7_310_000 and clicked_before_scroll() == []
          and events.count(("scroll", 1)) == 1, f"{job['bought']} for {job['paid']:,}; {events[:4]}")
    check("the log says why it moved on", "row 1 holds 10, and a Chaos Core row of 10 or fewer is never bought; "
          "moving on to the next row" in said, "")

    ok, job, said = take([(3, 690_000), (9, 695_000), (10, 700_000), (11, 731_000)])
    check("rows of 3, 9 and 10 are all passed over, and the row of 11 gives 10 with 1 left behind",
          ok and job["bought"] == 10 and events.count(("scroll", 1)) == 3, f"{job['bought']}; {events}")

    ok, job, said = take([(10, 700_000), (50, 731_000)], kind="special", shared=ON)
    check("the special row's own buy passes over a row of 10 too, so it never buys another special row",
          ok and job["bought"] == 10 and clicked_before_scroll() == [], f"{job['bought']}; {events[:4]}")

    ok, job, said = take([(10, 700_000), (50, 731_000)], take_all=0)
    check("an order that takes the row it is on whole still passes over a row of 10",
          ok and job["bought"] == 10 and clicked_before_scroll() == [], f"{job['bought']}; {events[:4]}")

    ok, job, said = take([(10, 700_000), (8, 702_000)])
    check("with nothing but rows of 10 or fewer down the steps, nothing is bought",
          not ok and job["bought"] == 0 and not any(e[0] == "click" for e in events), str(events))

    ok, job, said = take([(5, 250_000)], want=5, item=HIGH)
    check("a Force Core(High) row of 5 is bought as before", ok and job["bought"] == 5, f"{job['bought']}; {events}")

    events.clear()
    offers[:] = [{"name": CHAOS, "qty": 50, "price": 800_000, "unit_price": 800_000}]
    state.update(balance=10 ** 12, spend=10 * 800_000,
                 detail={"item": CHAOS, "price": 800_000, "qty": 1, "qty_max": 50})
    calibration.load_shared = lambda: copy.deepcopy(ON)
    with contextlib.redirect_stdout(io.StringIO()):
        out = buy._buy_row_one(SLOT[CHAOS], 10, balance=10 ** 12, special=True, leave_behind=1)
    calibration.load_shared = REAL_SHARED
    check("the special row's order for its 10 still buys over the 750,000 limit",
          out["bought"] == 10 and CONFIRM in [e[1] for e in events if e[0] == "click"], str(out))
finally:
    (buy.get_price.get_price, buy.show_work_tab, get_alz.read_balance, calibration.click, buy.await_dialog,
     buy.dialog_details, buy.dialog_button, buy.dialog_open, buy.await_balance, buy.time.sleep,
     calibration.park, calibration.snap, m.type_number, ledger.bought, buy.scroll_down,
     calibration.load_shared) = saved

orders = []
script = []


def fake_offers(job, want, batch, on_margin=True, verbose=True):
    orders.append(want)
    got = script.pop(0) if script else 0
    if got <= 0:
        return False
    job["bought"] += got
    job["paid"] += got * 731_000
    return True


listed = []
saved = (driver.take_offers, driver.work_tab_slots, driver.await_arrival, driver.special_list, calibration.load_shared)
driver.take_offers = fake_offers
driver.work_tab_slots = lambda verbose=True: set()
driver.await_arrival = lambda before, verbose=True: {(1, n) for n in range(1, 11)}
driver.special_list = lambda model, landing, core, first, last, verbose=True, cost=0, ordinal=0: listed.append(
    (core, cost)) or 5
calibration.load_shared = lambda: copy.deepcopy(ON)


def special_buy(fills):
    orders.clear()
    listed.clear()
    script[:] = list(fills)
    job = {"kind": "special", "core": CHAOS, "slot": SLOT[CHAOS], "landing": (1, 1), "step": "buy", "bought": 0,
           "paid": 0, "listed": 0, "rows": [], "ordinal": 0, "target": driver.special_qty(), "want_max": None,
           "sells_at": 0, "gap": None, "leave": 1, "steps_max": 1, "take_all": 10, "orders": 0}
    model = m.RowModel().seed({})
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            out = driver.finish_special(model, job, 1, 25, verbose=False)
        except buy.Refused as exc:
            out = exc
    return out, job


try:
    out, job = special_buy([10])
    check("the special row buys 10 in one order and lists them", orders == [10] and job["bought"] == 10
          and listed == [(CHAOS, 731_000)] and out["bought"] == 10, f"orders {orders}, listed {listed}")

    out, job = special_buy([7, 3])
    check("an order that gives only 7 is topped up with an order for the 3 short",
          orders == [10, 3] and job["bought"] == 10 and listed, f"orders {orders}")

    out, job = special_buy([7, 0])
    check("if the top-up buys nothing, the 7 bought are still listed", orders == [10, 3] and job["bought"] == 7
          and listed and not isinstance(out, Exception), f"orders {orders}, {out}")

    out, job = special_buy([0])
    check("nothing bought: nothing listed", isinstance(out, buy.Refused) and not listed, str(out))
finally:
    (driver.take_offers, driver.work_tab_slots, driver.await_arrival, driver.special_list,
     calibration.load_shared) = saved

calibration.load_shared = lambda: copy.deepcopy(ON)
saved = (driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep, driver.random.randint)
driver.back_to_the_shop = lambda verbose=True: True
driver.random.randint = lambda a, b: 0
driver.register_tab = lambda verbose=True: None
calibration.click = lambda *a, **k: None
m.time.sleep = lambda s: None
try:
    m.LAST_CHECKED.clear()
    m.note_checked(SET, 732_407)
    asked = []
    model = m.RowModel().seed({})
    model.release_work = lambda slot: None
    model.list_slot = lambda *a, **kw: asked.append(kw) or {"qty": 10, "price": kw.get("price") or 0, "units": 10}
    with contextlib.redirect_stdout(io.StringIO()):
        lands_in = driver.special_list(model, (1, 1), CHAOS, 1, 25, verbose=False, cost=731_000)
    row = model._slots.get(lands_in)
    at = 732_407 - ON["resupply"]["special_row"]["under_set_market"][CHAOS]
    check(f"all 10 go out together at the special row price, {at:,} each",
          row is not None and row.qty == 10 and row.price == at and asked[0].get("price") == at,
          f"{row.qty if row else None} at {row.price if row else None}")
    check("and the row of 10 is still known as the special row", driver.special_of(row) == CHAOS,
          str(driver.special_of(row)))
    check("a special listing of exactly 10 is the special row's own, free of the 650,000 listing minimum",
          m.special_single(CHAOS, 10) and not m.special_single(CHAOS, 9), "")
finally:
    calibration.load_shared = REAL_SHARED
    driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep, driver.random.randint = saved

model = m.RowModel().seed({})
check("with special rows off, as on every machine now, no special row is wanted, so nothing buys 10",
      driver.special_wanted(model, 1, 30) == [], str(driver.special_wanted(model, 1, 30)))

driver.holds_turn = held_turn
check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
