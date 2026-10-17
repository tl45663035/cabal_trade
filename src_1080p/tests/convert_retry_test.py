import contextlib
import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src_1080p"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("CABAL_CONFIG", "bot")

import no_input
TRIPPED = no_input.arm()
import calibration
import convert
import row_model
import time

CORE = "Upgrade Core (Ultimate)"
CELL = {"cell": "4x5", "point": (377, 931), "costs": "Upgrade Core Set (Ultimate)"}
QTY = calibration._centre(tuple(calibration._REG["convert_dialog_qty"]))
TAB = (1617, 181)
OK = (965, 677)
CANCEL = (1098, 677)
HELD = {(1, 1)}
LANDED = {(1, 2)}


class Screen:
    def __init__(self, gone_after_qty):
        self.gone_after_qty = set(gone_after_qty)
        self.attempt = 0
        self.up = False
        self.actions = []


S = Screen(())


def alt_click(x, y, settle=None):
    S.attempt += 1
    S.up = True
    S.actions.append(("alt", x, y))


def click(x, y, settle=None, **_k):
    S.actions.append(("click", x, y))
    if (x, y) == QTY and S.attempt in S.gone_after_qty:
        S.up = False


def dialog_button(word, image=None):
    if not S.up:
        return None
    return OK if word == convert.CONFIRM_WORD else CANCEL


convert.cell_for = lambda name: CELL
convert.await_dialog = lambda timeout=None: S.up
convert.dialog_button = dialog_button
convert.dialog_open = lambda image=None: S.up
convert.open_vendor = lambda verbose=True: S.actions.append(("vendor",))
convert._await_arrivals = lambda before, timeout=None: set(LANDED)
calibration.alt_click = alt_click
calibration.click = click
calibration.grab = lambda *a, **k: None
calibration.read_line = lambda image, box: f"* {CORE}"
calibration.inventory_tab_point = lambda tab: TAB
calibration.occupied_slots = lambda *a, **k: set(HELD)
calibration.park = lambda *a, **k: S.actions.append(("park",))
calibration.steps_table = lambda *a, **k: None
row_model.type_number = lambda value, clear: S.actions.append(("type", value))
time.sleep = lambda s: None

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def run(gone_after_qty):
    global S
    S = Screen(gone_after_qty)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            out, error = convert.convert(CORE, verbose=True), None
        except convert.Refused as exc:
            out, error = None, exc
    return out, error, said.getvalue()


def count(kind, point=None):
    return sum(1 for a in S.actions
               if a[0] == kind and (point is None or a[1:] == point))


out, error, said = run({1})
check(error is None, f"the 19:21 case converts on the retry, got {error!r}")
check(out is not None and out["slots"] == [min(LANDED)],
      f"and the core lands in the slot free before it, got {out!r}")
check(count("alt") == 2, f"the cell is Alt+clicked twice, got {count('alt')}")
check(count("vendor") == 1, f"the vendor is reopened once, got {count('vendor')}")
check(count("click", OK) == 1, f"OK is clicked once, got {count('click', OK)}")
first_type = S.actions.index(("type", convert.MAX_STACK))
check(S.actions[first_type + 1:first_type + 4]
      == [("click",) + TAB, ("vendor",), ("alt",) + CELL["point"]],
      f"after the missing OK it clicks tab 4, reopens the vendor and Alt+clicks "
      f"again, got {S.actions[first_type + 1:first_type + 4]}")
check(f"attempt 1/{convert.RETRIES}: no {convert.CONFIRM_WORD} button on the dialog; "
      f"clicking inventory tab {convert.INVENTORY_TAB} and converting again" in said,
      "the log says why it converts again")

out, error, said = run(range(1, convert.RETRIES + 1))
check(isinstance(error, convert.Refused) and not isinstance(error, convert.NoOffer),
      f"a missing OK on every attempt is refused, got {error!r}")
check(f"no {convert.CONFIRM_WORD} button on the dialog in {convert.RETRIES} attempt(s). "
      f"Nothing converted." == str(error), f"with the attempts named, got {error}")
check(count("alt") == convert.RETRIES,
      f"after {convert.RETRIES} Alt+clicks, got {count('alt')}")
check(count("click", OK) == 0, "and OK is never clicked")

out, error, said = run(())
check(error is None and out is not None, f"a dialog that stays converts first time, got {error!r}")
check(count("alt") == 1 and count("vendor") == 0,
      f"with one Alt+click and no reopen, got {count('alt')} and {count('vendor')}")
check(count("click", OK) == 1, "and one OK")
check("converting again" not in said, "and says nothing about a retry")

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
