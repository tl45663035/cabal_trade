import contextlib
import datetime
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_table_lost_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import open_agent_shop_premium as shop
import row_model as m
import war

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


class Table:
    def __init__(self, top):
        self.top = top
        self.wheels = []

    def wheel(self, notches, verbose=True):
        self.wheels.append(notches)
        self.top = max(1, min(m.MAX_TOP, self.top + notches))
        return abs(notches)

    def shop_opened(self):
        self.top = 1


def board(top):
    model = m.RowModel().seed({}, top=top)
    model._seat = m.FIRST_SEAT
    return model


def war_wait():
    start = datetime.datetime(2026, 9, 28, 22, 28, 0)
    saved = (war.ENABLED, war.now, war.quiet_window, calibration.close_everything)
    war.ENABLED = True
    war.now = lambda verbose=False: start
    war.quiet_window = lambda at: (start, start + datetime.timedelta(milliseconds=20))
    calibration.close_everything = lambda *a, **k: None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            return war.avoid(verbose=True)
    finally:
        war.ENABLED, war.now, war.quiet_window, calibration.close_everything = saved


def server_wait():
    answers = iter([True, False])
    saved = (calibration.server_busy, calibration.snap, calibration.time.sleep, calibration._recovered)
    calibration.server_busy = lambda *a, **k: next(answers)
    calibration.snap = lambda *a, **k: None
    calibration.time.sleep = lambda s: None
    calibration._recovered = lambda: None
    try:
        return calibration.wait_out_server_lag(verbose=False)
    finally:
        calibration.server_busy, calibration.snap, calibration.time.sleep, calibration._recovered = saved


def open_shop(table):
    saved = (shop.focus_game, shop.ensure_inventory_open, shop.click, shop.panel_open, shop.right_click,
             shop.tab_point, shop.slot_point, calibration._trade_window_open)
    shop.focus_game = lambda: True
    shop.ensure_inventory_open = lambda verbose=True: None
    shop.click = lambda *a, **k: None
    shop.panel_open = lambda *a, **k: True
    shop.right_click = lambda *a, **k: None
    shop.tab_point = lambda tab: (0, 0)
    shop.slot_point = lambda row, col: (0, 0)
    calibration._trade_window_open = lambda *a, **k: True
    try:
        shop.open_agent_shop(verbose=False)
        table.shop_opened()
    finally:
        (shop.focus_game, shop.ensure_inventory_open, shop.click, shop.panel_open, shop.right_click,
         shop.tab_point, shop.slot_point, calibration._trade_window_open) = saved


saved = (m.wheel, m.time.sleep)
m.time.sleep = lambda s: None
try:
    calibration.take_table_lost()
    table = Table(3)
    m.wheel = table.wheel
    model = board(3)
    with contextlib.redirect_stdout(io.StringIO()):
        model.scroll_to(4, verbose=False)
    check("with nothing in between, row 3 to row 4 is one notch down and no trip to the top",
          table.wheels == [1] and model.top == 4 == table.top, f"{table.wheels}, model {model.top}, table {table.top}")

    for label, event in (("a war wait", lambda t: war_wait()),
                         ("a server wait", lambda t: server_wait())):
        table = Table(3)
        m.wheel = table.wheel
        model = board(3)
        event(table)
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            model.scroll_to(4, verbose=False)
        check(f"after {label}, the next scroll goes all the way up first, then down to row 4",
              table.wheels == [-m.HOME_NOTCHES, 3] and model.top == 4 == table.top,
              f"{table.wheels}, model {model.top}, table {table.top}")
        check(f"and it says so in the log after {label}", "scrolling all the way first" in said.getvalue(),
              said.getvalue().strip())
        table.wheels.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            model.scroll_to(5, verbose=False)
        check(f"after {label} it goes to the top only once", table.wheels == [1] and model.top == 5,
              f"{table.wheels}, model {model.top}")

    table = Table(3)
    m.wheel = table.wheel
    model = board(3)
    war_wait()
    open_shop(table)
    with contextlib.redirect_stdout(io.StringIO()):
        model.scroll_to(4, verbose=False)
    check("15:34 replayed: row 3 on top, war wait, shop reopened at row 1, then row 4 is really on top",
          model.top == 4 == table.top, f"wheels {table.wheels}, model {model.top}, table {table.top}")

    table = Table(3)
    m.wheel = table.wheel
    model = board(3)
    saved_opened = table.shop_opened
    table.shop_opened = lambda: None
    open_shop(table)
    table.shop_opened = saved_opened
    with contextlib.redirect_stdout(io.StringIO()):
        model.scroll_to(4, verbose=False)
    check("opening the Agent Shop alone, as after a craft (the table keeps its place), costs no trip to the top",
          table.wheels == [1] and model.top == 4 == table.top, f"{table.wheels}, model {model.top}, table {table.top}")

    table = Table(3)
    m.wheel = table.wheel
    model = board(3)
    table.shop_opened()
    with contextlib.redirect_stdout(io.StringIO()):
        model.scroll_to(4, verbose=False)
    check("what is left uncaught: a reset with no war or server wait shows row 2 where the run believes row 4",
          table.top == 2 and model.top == 4, f"model {model.top}, table {table.top}")

    table = Table(3)
    m.wheel = table.wheel
    model = board(3)
    calibration.table_lost()
    with contextlib.redirect_stdout(io.StringIO()):
        model.home(verbose=False)
        model.scroll_to(4, verbose=False)
    check("a home clears the mark, so there is no second trip to the top",
          table.wheels == [-m.HOME_NOTCHES, 3] and model.top == 4, f"{table.wheels}, model {model.top}")

    far = m.CAPACITY
    table = Table(3)
    m.wheel = table.wheel
    model = board(3)
    calibration.table_lost()
    with contextlib.redirect_stdout(io.StringIO()):
        model.scroll_to(far, verbose=False)
    check(f"for row {far} (read at the last position) it goes all the way down instead",
          table.wheels[0] == m.HOME_NOTCHES and model.top == m.MAX_TOP == table.top,
          f"{table.wheels}, model {model.top}, table {table.top}")
finally:
    m.wheel, m.time.sleep = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
