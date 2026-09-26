import contextlib
import inspect
import io
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_overlap_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import row_model

m = row_model
CLICK, READ = 0.8, 0.3
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


class Board:
    def __init__(self, held):
        self.held = held

    def get(self, index):
        return object() if self.held else None

    def read(self):
        time.sleep(READ)
        return "Force Core (Ultimate) 49 399,553 On Sale Change"

    def button(self, image=None):
        time.sleep(READ)
        return m.CHANGE_WORD if self.held else m.REGISTER_WORD


clicks = []


def slow_tab(verbose=False, already=False):
    clicks.append(("start", time.perf_counter(), threading.current_thread().name))
    time.sleep(CLICK)
    clicks.append(("end", time.perf_counter()))


saved = (m.show_work_tab, driver.row_from_screen)
m.show_work_tab = slow_tab
driver.row_from_screen = lambda text, model: (text, "row")
try:
    calibration.steps_reset()
    started = time.perf_counter()
    text, row, button, selected = driver.read_the_row(Board(True), 5)
    took = time.perf_counter() - started
    finished = clicks[-1][1] if clicks and clicks[-1][0] == "end" else None
    check("a listed row: tab 4 is clicked while the row and its button are read",
          selected and button == m.CHANGE_WORD and took < CLICK + READ
          and clicks[0][2] != threading.current_thread().name,
          f"{took * 1000:.0f} ms against {(CLICK + 2 * READ) * 1000:.0f} ms one after another")
    check("and the click has finished before anything else happens",
          finished is not None and finished <= started + took)
    clicks.clear()
    started = time.perf_counter()
    text, row, button, selected = driver.read_the_row(Board(False), 5)
    took = time.perf_counter() - started
    check("an empty row: no tab 4 click, only its button is read",
          not selected and clicks == [] and button == m.REGISTER_WORD and took < READ + CLICK / 2,
          f"{took * 1000:.0f} ms")
finally:
    m.show_work_tab, driver.row_from_screen = saved

events = []
saved = (m.read_row, m.row_button, m.show_work_tab, m.find_button, m.dialog_gone, m.game_refused,
         m.inv._user32, calibration.click, calibration.park, m.time.sleep)
m.read_row = lambda seat=None: "Force Core (Ultimate) 49 399,553 On Sale Change"
m.row_button = lambda image=None, seat=None: None
m.show_work_tab = lambda verbose=False, already=False: events.append("tab 4")
m.find_button = lambda word, timeout=None, verbose=False: (1, 1)
m.dialog_gone = lambda timeout=None: True
refusals = []
m.game_refused = lambda done, timeout=None: bool(refusals and refusals.pop())
m.inv._user32 = type("Hover", (), {"SetCursorPos": staticmethod(lambda *a: None)})()
calibration.click = lambda x, y, settle=None: events.append("click")
calibration.park = lambda settle=True: events.append(f"park settle={settle}")
m.time.sleep = lambda s: events.append(f"wait {s}")
try:
    board = m.RowModel().seed({})
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    board.scroll_to = lambda index, verbose=True: {"moved": False}
    board._seat = m.FIRST_SEAT
    board.again_after_wait = lambda *a, **k: None
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True)
    check("the cancel skips its own tab 4 click when tab 4 was already selected",
          "tab 4" not in events, str(events))
    check("the row's button could not be read, so the row text decides it is a Change row",
          events.count("click") == 3, str(events))
    check("after Confirmation it parks without the pause",
          "park settle=False" in events and "park settle=True" not in events, str(events))
    check(f"and it does not wait {m.TAB_SETTLE:g} s after a scroll that did not move the table",
          f"wait {m.TAB_SETTLE}" not in events, str(events))
    events.clear()
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    refusals.append(True)
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True)
    check("when the game says wait and the cancel runs again, that second go selects tab 4 itself",
          events.count("tab 4") == 1, str(events))
    events.clear()
    board._slots[5] = m.Row("Force Core (Ultimate", qty=49, price=399553)
    board.scroll_to = lambda index, verbose=True: {"moved": True}
    with contextlib.redirect_stdout(io.StringIO()):
        board.cancel(5, verbose=False, tab_ready=True, tab_selected=True)
    check(f"it still waits {m.TAB_SETTLE:g} s when the table did move", f"wait {m.TAB_SETTLE}" in events, str(events))
finally:
    (m.read_row, m.row_button, m.show_work_tab, m.find_button, m.dialog_gone, m.game_refused,
     m.inv._user32, calibration.click, calibration.park, m.time.sleep) = saved

wheels = []
saved = m.wheel
m.wheel = lambda notches, verbose=True: wheels.append(notches)
try:
    board = m.RowModel().seed({})
    board.seat_of = lambda index: (m.FIRST_SEAT, index)
    board._top = 1
    board._seat = m.FIRST_SEAT
    board.scroll_plan = lambda index: {"notches": 0, "to_top": 1, "position": 1}
    still = board.scroll_to(5, verbose=False)
    board.scroll_plan = lambda index: {"notches": 2, "to_top": 3, "position": 1}
    moved = board.scroll_to(5, verbose=False)
finally:
    m.wheel = saved
check("scroll_to says the table did not move when no notch was needed",
      still["moved"] is False and still["position"] == 1, str(still))
check("and that it moved when it wheeled", moved["moved"] is True and wheels == [2], str((moved, wheels)))

grabs = []
saved = (calibration.grab, m.warm_money)
calibration.grab = lambda *a, **k: grabs.append("grab")
m.warm_money = lambda image, box: 399_552
try:
    with contextlib.redirect_stdout(io.StringIO()):
        m.check_price_field(399_552, 10, verbose=True)
finally:
    calibration.grab, m.warm_money = saved
check("the price read-back takes its own screenshot of the field, with no reading carried in",
      grabs == ["grab"] and list(inspect.signature(m.check_price_field).parameters)
      == ["want", "qty", "verbose"] and not hasattr(m, "read_price_field"), str(grabs))

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
