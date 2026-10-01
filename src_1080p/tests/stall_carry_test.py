import contextlib
import inspect
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_stall_carry_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import ledger

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None
fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


class Model:
    def home(self, verbose=True):
        walked.append("home")


walked = []
stall_at = {"row": None}


def relist_one(model, index, **kw):
    walked.append(index)
    if index == stall_at["row"]:
        stall_at["row"] = None
        raise calibration.ServerStalled("the server stalled for 30s; the pass starts again.")
    return None


driver.shop_ready = lambda *a, **k: None
driver.relist_one = relist_one
driver.board_trace = lambda *a, **k: None
driver.pending_holds_stock = lambda: False
driver.urgent_check = lambda *a, **k: None
driver.special_pass = lambda *a, **k: walked.append("special")
calibration.phases_reset = lambda *a, **k: None
calibration.phases_table = lambda *a, **k: None

stall_at["row"] = 12
try:
    driver.relist_pass(Model(), 1, 30)
    check("a stall in the walk ends the pass", False)
except calibration.ServerStalled:
    check("a stall in the walk ends the pass", True)
check("the walk got to row 12 before the stall", walked == ["home"] + list(range(1, 13)), str(walked))
check("the row the stall hit is remembered", driver._WALK["at"] == 12, str(driver._WALK["at"]))

walked.clear()
said = io.StringIO()
with contextlib.redirect_stdout(said):
    driver.relist_pass(Model(), 1, 30, start=driver._WALK["at"])
check("the next pass scrolls home, then carries on at row 12 and walks to the end",
      walked == ["home"] + list(range(12, 31)) + ["special"], str(walked))
check("and says so", "carrying on at row 12" in said.getvalue(), said.getvalue().strip())
check("a finished walk forgets the row", driver._WALK["at"] is None)

walked.clear()
driver.relist_pass(Model(), 1, 30, start=driver._WALK["at"])
check("the pass after a finished walk starts at row 1 again", walked == ["home"] + list(range(1, 31)) + ["special"],
      str(walked[:5]))

stall_at["row"] = 5
walked.clear()
try:
    driver.relist_pass(Model(), 1, 30, start=3)
except calibration.ServerStalled:
    pass
check("a second stall while carrying on remembers the newer row", driver._WALK["at"] == 5, str(driver._WALK["at"]))
driver._WALK["at"] = None

source = inspect.getsource(driver.do_relist)
check("the pass loop hands the remembered row to the walk", 'start=_WALK["at"]' in source)
check("a stall does not forget the row", '_WALK["at"] = None' not in source)
check("collecting still walks every row", "start" not in inspect.getsource(driver.do_collect))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
