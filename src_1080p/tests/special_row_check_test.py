import contextlib
import copy
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_special_check_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import ledger
import row_model as m

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


CHAOS = "Chaos Core"
REAL_SHARED = calibration.load_shared
ON = copy.deepcopy(REAL_SHARED())
ON["resupply"]["special_row"]["enabled"] = True
ON["resupply"]["special_row"]["takes_turns"][CHAOS] = False
EVERY = ON["resupply"]["urgent_check"][CHAOS]
calibration.load_shared = lambda: copy.deepcopy(ON)
driver._TURNS = None


def chaos_row(qty=13, price=517_382, cost=524_000):
    return m.Row(CHAOS, qty=qty, price=price, buy_cost=cost)


def board(*rows):
    model = m.RowModel().seed({})
    for index, row in rows:
        model._slots[index] = row
    return model


def walk(model, *steps):
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        for processed in steps:
            driver.special_turn(model, 1, 25, processed)
    return said.getvalue()


walked, bought = [], []


def unsold(model, index, verbose=True, first=None, last=None, collect_only=False):
    walked.append((index, collect_only))
    return False


def sold(model, index, verbose=True, first=None, last=None, collect_only=False):
    walked.append((index, collect_only))
    driver._FULL_SALES += 1
    model.drop(index)
    return None


def gone(model, index, verbose=True, first=None, last=None, collect_only=False):
    walked.append((index, collect_only))
    model.drop(index)
    return None


saved = (driver.relist_one, driver.special_pass, driver.alz_too_low)
driver.special_pass = lambda model, first, last, verbose=True: bought.append("bought back")
driver.alz_too_low = lambda: False
try:
    check("the special row does not take turns here, so no turn system is running",
          driver.turn_cores() == [] and driver._TURNS is None, str(driver.turn_cores()))
    check(f"the check comes every {EVERY} processed rows, from config", driver.special_check_every() == EVERY == 4,
          str(driver.special_check_every()))

    driver.relist_one = unsold
    driver._TURN_EVERY, driver._SINCE_SPECIAL = EVERY, 0
    driver._WALK["at"] = 7
    model = board((3, chaos_row()))
    walk(model, True, False, True, True)
    check("3 processed rows, with a skipped row not counting: no check yet", walked == [], str(walked))
    said = walk(model, True)
    check("the 4th processed row checks the special row in row 3, collecting only",
          walked == [(3, True)] and driver._SINCE_SPECIAL == 0, str(walked))
    check("and the log says so", "checking the Chaos Core special row in row 3" in said, said.strip())
    check("an unsold row is left where it is, and nothing is bought", bought == [], str(bought))
    walk(model, True, True, True, True)
    check("the next 4 processed rows check it again", walked == [(3, True), (3, True)], str(walked))

    walked.clear()
    driver._WALK["at"] = 3
    walk(model, True, True, True, True)
    check("the row the walk has just handled is not checked twice", walked == [], str(walked))

    walked.clear()
    driver._WALK["at"] = 7
    driver.relist_one = sold
    driver._SINCE_SPECIAL = EVERY - 1
    said = walk(model, True)
    check("the check finds it sold and buys it back at once", walked == [(3, True)] and bought == ["bought back"],
          f"{walked}; {bought}")
    check("the log says it is being bought back", "the Chaos Core special row sold; buying it back now" in said,
          said.strip())
    check("and says nothing about turns, which this machine does not use", "turn" not in said, said.strip())

    walked.clear()
    bought.clear()
    driver.relist_one = gone
    driver._SINCE_SPECIAL = EVERY - 1
    said = walk(board((3, chaos_row())), True)
    check("a row that vanished without a sale is listed again", bought == ["bought back"]
          and "no longer holds the Chaos Core special row; listing it again" in said, said.strip())

    walked.clear()
    bought.clear()
    driver.relist_one = unsold
    driver._SINCE_SPECIAL = EVERY - 1
    walk(board((3, m.Row("Chaos Core Set X 3", qty=1, price=1_600_000))), True)
    check("a board with no special row on it has nothing to check", walked == [], str(walked))

    off = copy.deepcopy(ON)
    off["resupply"]["urgent_check"][CHAOS] = 0
    calibration.load_shared = lambda: copy.deepcopy(off)
    check("with the check set to 0 in config, it is off", driver.special_check_every() == 0, "")
    driver._SINCE_SPECIAL = EVERY
    walk(board((3, chaos_row())), True)
    check("and no row is checked", walked == [], str(walked))
    calibration.load_shared = REAL_SHARED
    check("with special rows off there is no check at all", driver.special_check_every() == 0, "")
    calibration.load_shared = lambda: copy.deepcopy(ON)
finally:
    driver.relist_one, driver.special_pass, driver.alz_too_low = saved

real = (driver.read_the_row, m.refresh_table, driver.row_from_screen, calibration.snap)
driver.read_the_row = lambda model, index: ("Chaos Core 13 517,382 Receipt", chaos_row(), m.RECEIPT_WORD, True)
m.refresh_table = lambda model=None, verbose=False: None
driver.row_from_screen = lambda text, model: (text, m.Row(CHAOS, qty=1, price=517_382))
calibration.snap = lambda *a, **k: None
try:
    model = board((3, chaos_row()))
    model.scroll_to = lambda index, verbose=True: {"moved": False}
    model.complete = lambda text: False
    model.receive = lambda index, verbose=False, complete=False: ("Chaos Core 1 517,382 On Sale Change", m.CHANGE_WORD)
    cancelled = []
    model.cancel = lambda index, **kw: cancelled.append(index)
    with contextlib.redirect_stdout(io.StringIO()):
        out = driver.relist_one(model, 3, verbose=True, first=1, last=25, collect_only=True)
    row = model.get(3)
    check("a check that finds 12 of 13 sold collects them and leaves the last core listed",
          out is False and cancelled == [] and row.qty == 1, f"{out}; {cancelled}; {row}")
    check("the row keeps its price and what it cost", row.price == 517_382 and row.buy_cost == 524_000, str(row))
finally:
    driver.read_the_row, m.refresh_table, driver.row_from_screen, calibration.snap = real

real = (driver.relist_one, driver.board_trace, driver.pending_holds_stock, driver.urgent_check, driver.shop_ready,
        driver.special_pass, driver.special_turn)
driver.relist_one = lambda model, index, **kw: None
driver.board_trace = lambda *a, **k: None
driver.pending_holds_stock = lambda: False
driver.urgent_check = lambda *a, **k: None
driver.shop_ready = lambda *a, **k: True
driver.special_pass = lambda *a, **k: None
driver.special_turn = lambda *a, **k: None
try:
    model = board()
    model.home = lambda verbose=False: None
    driver._TURN_EVERY = 0
    with contextlib.redirect_stdout(io.StringIO()):
        driver.relist_pass(model, 1, 1)
    check("every pass reads the check from config, so switching the row on needs no restart",
          driver._TURN_EVERY == EVERY, str(driver._TURN_EVERY))
finally:
    (driver.relist_one, driver.board_trace, driver.pending_holds_stock, driver.urgent_check, driver.shop_ready,
     driver.special_pass, driver.special_turn) = real

said = io.StringIO()
with contextlib.redirect_stdout(said):
    driver.start_turns()
check("the run says at start-up how often it checks", "the special row is checked every 4 processed row(s)"
      in said.getvalue() and driver._TURNS is None, said.getvalue().strip())
calibration.load_shared = REAL_SHARED
said = io.StringIO()
with contextlib.redirect_stdout(said):
    driver.start_turns()
check("with special rows off it says nothing", said.getvalue() == "" and driver._TURN_EVERY == 0, said.getvalue())

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
