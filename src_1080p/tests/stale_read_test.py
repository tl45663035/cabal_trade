import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_stale_read_")) / "sales.db")
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


class Cancelled(Exception):
    pass


BEFORE = "Divine Stone 2 706,512 On Sale Change"
AFTER = "Divine Stone 0 706,512 Complete Receive"
calls = []
reads = []
buttons = []
snaps = []
shots = []
seen_by = {}


def read_the_row(model, index):
    calls.append("read")
    return reads.pop(0)


def grab():
    shots.append(object())
    return shots[-1]


def button(image=None):
    seen_by.setdefault("button", []).append(image)
    return buttons.pop(0)


def cancel(index, **kw):
    calls.append(("cancel", kw.get("read")))
    raise Cancelled()


def receive(index, verbose=True, complete=False, settle=False, listed=None):
    calls.append(("receive", complete))
    return "", m.REGISTER_WORD


saved = (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
         driver.restock_now, calibration.grab, calibration.snap, m.panel_holds_item, m.panel_standing)
driver.read_the_row = read_the_row
m.refresh_table = lambda model=None, verbose=False: calls.append("refresh")
driver.reconcile_work_tab = lambda model, verbose=True: None
driver.tab_before_withdrawal = lambda model, verbose=True: None
driver.restock_now = lambda *a, **k: calls.append("restock")
calibration.grab = grab
calibration.snap = lambda name, *a, **k: snaps.append(name)
m.panel_holds_item = lambda image=None: seen_by.setdefault("panel", []).append(image) or False
m.panel_standing = lambda *a, **k: None


def board():
    model = m.RowModel().seed({})
    model._slots[1] = m.Row("Divine Stone", qty=2, price=706512, buy_cost=700000)
    model._seat = m.FIRST_SEAT
    model.scroll_to = lambda index, verbose=True: calls.append("scroll") or {"moved": False}
    model.next_work_slot = lambda: (1, 1)
    model.button = button
    model.cancel = cancel
    model.receive = receive
    return model


def listed():
    return m.Row("Divine Stone", qty=2, price=706512)


def run(model):
    calls.clear()
    snaps.clear()
    shots.clear()
    seen_by.clear()
    said = io.StringIO()
    raised = None
    with contextlib.redirect_stdout(said):
        try:
            out = driver.relist_one(model, 1, verbose=True, first=1, last=25)
        except (Cancelled, m.Divergence) as exc:
            out, raised = None, exc
    return out, raised, said.getvalue()


try:
    model = board()
    reads[:] = [(BEFORE, listed(), m.CHANGE_WORD, True)]
    buttons[:] = [m.CHANGE_WORD]
    out, raised, said = run(model)
    check("a row whose button still says Change goes on to the cancel with the relist's own read",
          isinstance(raised, Cancelled) and calls[-1] == ("cancel", (BEFORE, m.CHANGE_WORD)), str(calls))
    check("the button is read again from the screenshot the shop-slot check already took, so no extra screenshot",
          len(shots) == 1 and seen_by["button"] == [shots[0]] and seen_by["panel"] == [shots[0]],
          f"{len(shots)} screenshot(s)")
    check("and the button read has its own line in the step table",
          any(label == f"read the button again before {m.CHANGE_WORD}" for label, _ms in calibration._STEPS),
          str([label for label, _ms in calibration._STEPS]))

    model = board()
    reads[:] = [(BEFORE, listed(), m.CHANGE_WORD, True), (AFTER, None, m.RECEIPT_WORD, True)]
    buttons[:] = [m.RECEIPT_WORD]
    out, raised, said = run(model)
    check("the 00:12:55 case: the read came before the refresh, the button now says Receive; nothing is cancelled",
          raised is None and not any(isinstance(c, tuple) and c[0] == "cancel" for c in calls), str(calls))
    check("the row starts over once from the Refresh, and that pass collects the sale",
          calls.count("refresh") == 2 and ("receive", True) in calls and out is None and model.get(1) is None,
          str(calls))
    check("the log says so and a frame is kept",
          "Starting the row over" in said and snaps == ["row_1_button_turned_receive"],
          f"{said.strip().splitlines()[-3:]}, {snaps}")

    model = board()
    reads[:] = [(BEFORE, listed(), m.CHANGE_WORD, True), ("", None, m.REGISTER_WORD, True)]
    buttons[:] = [m.REGISTER_WORD]
    out, raised, said = run(model)
    check("a button that now says Register also starts the row over, and the row is found empty",
          raised is None and calls.count("refresh") == 2 and out is None and model.get(1) is None
          and not any(isinstance(c, tuple) for c in calls), str(calls))

    model = board()
    reads[:] = [(BEFORE, listed(), m.CHANGE_WORD, True), (BEFORE, listed(), m.CHANGE_WORD, True)]
    buttons[:] = [m.RECEIPT_WORD, m.RECEIPT_WORD]
    out, raised, said = run(model)
    check("if it happens a second time running, the run stops before anything is cancelled",
          isinstance(raised, m.Divergence) and "twice running" in str(raised)
          and not any(isinstance(c, tuple) and c[0] == "cancel" for c in calls) and calls.count("refresh") == 2,
          f"{raised}; {calls}")

    model = board()
    reads[:] = [(BEFORE, listed(), m.CHANGE_WORD, True)]
    buttons[:] = [None]
    out, raised, said = run(model)
    check("a button that does not read leaves the relist as it was",
          isinstance(raised, Cancelled) and calls.count("refresh") == 1 and not snaps, str(calls))
finally:
    (driver.read_the_row, m.refresh_table, driver.reconcile_work_tab, driver.tab_before_withdrawal,
     driver.restock_now, calibration.grab, calibration.snap, m.panel_holds_item, m.panel_standing) = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
