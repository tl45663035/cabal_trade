import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_special_turns_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import ledger
import messaging
import row_model as m
from fake_broker import Broker, Client

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


CHAOS, KEY = "Chaos Core", m.item_key("Chaos Core")
REAL_SHARED = calibration.load_shared
ON = copy.deepcopy(REAL_SHARED())
ON["resupply"]["special_row"]["enabled"] = True
CONF = ON["messaging"]
ACCOUNT = {"host": "broker.test", "port": 8883, "secret": "5e" * 32}
P = CONF["topic_prefix"]
calibration.load_shared = lambda: copy.deepcopy(ON)
broker = Broker()
said = []


def make(client_id):
    return Client(broker, client_id)


def peer(name):
    return messaging.Participant(name, True, make=make, account=ACCOUNT, conf=CONF)


def chaos_row(price=725_407, qty=10, cost=731_000):
    return m.Row(CHAOS, qty=qty, price=price, buy_cost=cost)


def board(*rows):
    model = m.RowModel().seed({})
    for index, row in rows:
        model._slots[index] = row
    return model


def grant():
    return json.loads(broker.retained[f"{P}/grant"])["m"]


try:
    driver._TURNS = None
    check("the Chaos special row takes turns; with special rows on it is the turn core",
          driver.turn_cores() == [CHAOS], str(driver.turn_cores()))
    check("with no messaging link, this machine never holds the Chaos special row", driver.special_names() == {}, "")
    for price in (690_000, 729_999, 730_000, 740_000, 800_000):
        m.LAST_MARKET[KEY] = price
        check(f"one special row at a Chaos price of {price:,}", driver.special_rows(CHAOS) == 1,
              str(driver.special_rows(CHAOS)))

    boss = messaging.Orchestrator(said.append, make=make, account=ACCOUNT, conf=CONF, start=False)
    boss.begin()
    bot, trung = peer("bot"), peer("trung")
    driver._TURNS, driver._TURN_EVERY, driver._SINCE_SPECIAL = bot, 4, 0
    check("bot holds turn 1: the Chaos special row is bot's", driver.holds_turn()
          and driver.special_names() == {KEY: CHAOS}, str(grant()))
    check("an empty board wants its one special row", driver.special_wanted(board(), 1, 25) == [CHAOS], "")
    check("a board with the special row on it wants none", driver.special_wanted(board((3, chaos_row())), 1, 25) == [],
          "")
    check("a Chaos row of 10 is the special row while bot holds the turn",
          driver.special_of(chaos_row()) == CHAOS, str(driver.special_of(chaos_row())))

    calls = []
    saved = (driver.special_pass, driver.relist_one)
    driver.special_pass = lambda model, first, last, verbose=True: calls.append(("special_pass",))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.special_turn(board(), 1, 25, True)
        driver.special_turn(board(), 1, 25, True)
    check("bot's new turn lists the special row once, at the next row", calls == [("special_pass",)], str(calls))
    check("and the log says why", "holds the special row's turn; listing it now" in out.getvalue(), out.getvalue())

    calls.clear()
    walked = []

    def unsold(model, index, verbose=True, first=None, last=None, collect_only=False):
        walked.append((index, collect_only))
        return False

    driver.relist_one = unsold
    model = board((3, chaos_row()))
    driver._SINCE_SPECIAL = 0
    driver._WALK["at"] = 7
    with contextlib.redirect_stdout(io.StringIO()):
        for processed in (True, False, True, True):
            driver.special_turn(model, 1, 25, processed)
    check("3 processed rows (a skipped row does not count): no check yet", walked == [], str(walked))
    with contextlib.redirect_stdout(io.StringIO()):
        driver.special_turn(model, 1, 25, True)
    check("the 4th processed row checks the special row in row 3, collecting only",
          walked == [(3, True)] and driver._SINCE_SPECIAL == 0, str(walked))
    check("an unsold special row sends nothing", grant()["holder"] == "bot", str(grant()))

    walked.clear()
    driver._WALK["at"] = 3
    driver._SINCE_SPECIAL = 4
    with contextlib.redirect_stdout(io.StringIO()):
        driver.special_turn(model, 1, 25, True)
    check("the row the walk just relisted is not checked again", walked == [], str(walked))

    def sold(model, index, verbose=True, first=None, last=None, collect_only=False):
        walked.append((index, collect_only))
        driver._FULL_SALES += 1
        model.drop(index)
        return None

    walked.clear()
    driver.relist_one = sold
    driver._WALK["at"] = 7
    driver._SINCE_SPECIAL = 3
    out = io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(out):
        driver.special_turn(model, 1, 25, True)
    took = (time.perf_counter() - started) * 1000
    check(f"the check finds it sold out: every script is told and Trung holds the next turn ({took:.0f} ms here)",
          walked == [(3, True)] and grant()["holder"] == "trung" and not driver.holds_turn() and trung.holds(),
          f"{grant()}; {out.getvalue().strip()}")
    check("bot does not buy it back: the next turn is Trung's", calls == [], str(calls))
    check("its log says the right passes on", "sold out; every script is told, and the right passes on after turn 1"
          in out.getvalue(), out.getvalue().strip())

    walked.clear()
    driver.relist_one = unsold
    driver._SINCE_SPECIAL = 10
    with contextlib.redirect_stdout(io.StringIO()):
        driver.special_turn(board((3, chaos_row())), 1, 25, True)
    check("without the turn, bot checks no special row", walked == [], str(walked))
    check("and its old Chaos row of 10 is no longer special", driver.special_of(chaos_row()) is None, "")

    trung.sold(CHAOS)
    check("Trung's row sells out: the turn comes back to bot", grant()["holder"] == "bot" and driver.holds_turn(),
          str(grant()))
    alz = []
    saved_alz = driver.alz_too_low
    driver.alz_too_low = lambda: alz.append("read") or False
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.restock_now(board(), CHAOS, 1, 25)
    driver.alz_too_low = saved_alz
    check("the relist finds bot's special row sold: the turn passes to Trung before any balance read, no buy-back",
          grant()["holder"] == "trung" and alz == [] and calls == [], f"{grant()}; {alz}; {calls}")

    driver.special_pass, driver.relist_one = saved

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

    try:
        screen.update(text="Chaos Core 10 725,407 On Sale Change", row=m.Row(CHAOS, qty=10, price=725_407))
        model = board((3, chaos_row()))
        model._seat = m.FIRST_SEAT
        model.scroll_to = lambda index, verbose=True: {"moved": False}
        model.next_work_slot = lambda: (1, 1)
        model.button = lambda image=None: m.CHANGE_WORD
        model.cancel = lambda index, **kw: None
        model.list_slot = list_back
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                driver.relist_one(model, 3, verbose=True, first=1, last=25)
            except Stop:
                pass
        kw = asked[0] if asked else {}
        check("with the turn gone, bot relists its old row as a normal Chaos row: market price, no lower than its cost",
              kw.get("special") is False and kw.get("price") is None and kw.get("floor") == 731_000
              and kw.get("under") is None, str({k: kw.get(k) for k in ("special", "price", "floor", "under", "why")}))
    finally:
        (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
         driver.select_after_withdrawal, calibration.grab, m.panel_holds_item, calibration.snap,
         calibration.steps_table, calibration.park) = saved

    asked.clear()
    saved = (driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep)
    driver.back_to_the_shop = lambda verbose=True: True
    driver.register_tab = lambda verbose=True: None
    calibration.click = lambda *a, **k: None
    m.time.sleep = lambda s: None
    try:
        model = board()
        model.release_work = lambda slot: None
        model.list_slot = lambda *a, **kw: asked.append(kw) or {"qty": 10, "price": 731_000, "units": 10}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            driver.special_list(model, (1, 1), CHAOS, 1, 25, verbose=False, cost=731_000)
        kw = asked[0] if asked else {}
        check("cores bought for the special row after the turn moved on go out as a normal row, no lower than cost",
              kw.get("special") is False and kw.get("price") is None and kw.get("floor") == 731_000,
              str({k: kw.get(k) for k in ("special", "price", "floor")}))
        check("and the log says so", "turn moved on while it was bought" in out.getvalue(), out.getvalue().strip())
    finally:
        driver.back_to_the_shop, driver.register_tab, calibration.click, m.time.sleep = saved

    trung.sold(CHAOS)
    check("Trung sells: bot holds again", driver.holds_turn(), str(grant()))
    broker.drop(next(c for c in broker.clients if c.client_id == f"{P}-orchestrator"))
    check("bot's orchestrator goes offline: nobody holds the Chaos special row", not driver.holds_turn()
          and not trung.holds() and driver.special_names() == {}, "")

    idle = board((3, chaos_row()))
    driver._SINCE_SPECIAL = 0
    started = time.perf_counter()
    reps = 2_000
    with contextlib.redirect_stdout(io.StringIO()):
        for _ in range(reps):
            driver._SINCE_SPECIAL = 0
            driver.special_turn(idle, 1, 25, True)
    each = (time.perf_counter() - started) / reps * 1e6
    check(f"a row with nothing to do for the special row costs {each:.1f} us", each < 200, f"{each:.1f} us")

    seen = []
    saved = (driver.relist_one, driver.board_trace, driver.pending_holds_stock, driver.urgent_check,
             driver.shop_ready, driver.special_pass, driver.special_turn)
    driver.relist_one = lambda model, index, **kw: {1: True, 2: True, 3: None}[index]
    driver.board_trace = lambda *a, **k: None
    driver.pending_holds_stock = lambda: False
    driver.urgent_check = lambda *a, **k: seen.append("margin check")
    driver.shop_ready = lambda *a, **k: True
    driver.special_pass = lambda *a, **k: seen.append("end of pass")
    driver.special_turn = lambda model, first, last, processed, verbose=True: seen.append(("turn", processed))
    try:
        model = board()
        model.home = lambda verbose=False: None
        with contextlib.redirect_stdout(io.StringIO()):
            driver.relist_pass(model, 1, 3)
        check("the walk runs the margin check, then the special row's turn, after every row",
              seen == ["margin check", ("turn", True), "margin check", ("turn", True), ("turn", False), "end of pass"],
              str(seen))
    finally:
        (driver.relist_one, driver.board_trace, driver.pending_holds_stock, driver.urgent_check,
         driver.shop_ready, driver.special_pass, driver.special_turn) = saved

    driver._TURNS = None
    out = io.StringIO()
    real_client = messaging.paho_client
    messaging.paho_client = lambda: make
    try:
        with contextlib.redirect_stdout(out):
            driver.start_turns()
    finally:
        messaging.paho_client = real_client
    joined = driver._TURNS
    check("with special rows on, the run joins the turns through the public broker with nothing to set up",
          joined is not None and joined.link.up and "takes turns between bot, trung, cang" in out.getvalue()
          and "checks it every 4 processed row(s)" in out.getvalue(), out.getvalue().strip())
    driver.stop_turns()
    check("and leaves cleanly at the end of the run, saying it is offline", driver._TURNS is None
          and json.loads(broker.retained[f"{P}/presence/bot"])["m"]["online"] is False, "")
    calibration.load_shared = REAL_SHARED
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        driver.start_turns()
    check("with special rows off, as on every machine now, the run does not join the turns at all",
          driver._TURNS is None and out.getvalue() == "", out.getvalue().strip())
finally:
    calibration.load_shared = REAL_SHARED
    driver._TURNS = None

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
