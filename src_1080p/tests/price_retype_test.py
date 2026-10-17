import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_price_retype_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))

import no_input
TRIPPED = no_input.arm()
import calibration
import ledger
import row_model as m

ledger.DB = Path(os.environ["CABAL_SALES_DB"])
ledger._RUN = None

WANT = 194_950_000
MARKET = 194_400_000
PANEL = m._shop()["panel"]
PRICE_POINT = tuple(PANEL["price_point"])
QTY_POINT = tuple(PANEL["qty_point"])
REGISTER = tuple(PANEL["register_button"])
CONFIRM = (968, 634)


class Screen:
    def __init__(self, bad):
        self.bad = set(bad)
        self.checks = 0
        self.listed = False
        self.events = []


S = Screen(())


def click(x, y, **_k):
    S.events.append(("click", x, y))
    if (x, y) == CONFIRM:
        S.listed = True


def net(want, verbose=False):
    S.checks += 1
    S.events.append(("net", want))
    return None if S.checks in S.bad else 1


saved = (calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
         m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
         m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
         calibration.steps_table, m.time.sleep, calibration.snap)
calibration.grab = lambda *a, **k: object()
m.panel_holds_item = lambda image=None: False
calibration.slot_is_empty = lambda image, r, c: S.listed
calibration.ctrl_click = lambda *a, **k: None
m.suggested_price = lambda verbose=False, listed_at=None, overlap=False: MARKET
calibration.click = click
m.type_number = lambda value, clear: S.events.append(("type", value, clear))
calibration.park = lambda *a, **k: S.events.append(("park",))
m.panel_quantity = net
m.check_price_field = lambda *a, **k: None
m.find_button = lambda *a, **k: CONFIRM
m.underprice_warning = lambda image=None: False
m.dialog_gone = lambda timeout=None: True
calibration.occupied_slots = lambda image=None: set()
calibration.steps_table = lambda *a, **k: None
m.time.sleep = lambda s: None
calibration.snap = lambda label, *a, **k: S.events.append(("snap", label))

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def run(bad):
    global S
    S = Screen(bad)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        try:
            out, error = m.RowModel().seed({})._list_slot(1, 1, price=WANT, floor=0, verbose=True,
                                                          resolve=False), None
        except m.Divergence as exc:
            out, error = None, exc
    return out, error, said.getvalue()


def typed():
    return [e[1] for e in S.events if e[0] == "type"]


def count(kind, *rest):
    return sum(1 for e in S.events if e[0] == kind and (not rest or e[1:] == rest))


def after_check(n):
    seen = 0
    for i, e in enumerate(S.events):
        if e[0] == "net":
            seen += 1
            if seen == n:
                return S.events[i + 1:i + 6]
    return []


check(m.PRICE_RETYPES == calibration.load_shared()["run"]["price_retypes"] == 3,
      f"three retypes, from config, got {m.PRICE_RETYPES}")

out, error, said = run({1})
check(error is None and out is not None and out["price"] == WANT and out["qty"] == 1,
      f"the 22:24 case lists on the first retype, got {out!r} {error!r}")
check(typed() == [WANT, m.MAX_STACK, WANT],
      f"it types the price, the quantity, then the price again, got {typed()}")
check(after_check(1) == [("snap", "panel_will_not_confirm"), ("click",) + PRICE_POINT,
                         ("type", WANT, m.CLEAR_PRESSES_PRICE), ("park",), ("net", WANT)],
      f"after the failed check it clicks the price box, clears it with {m.CLEAR_PRESSES_PRICE} presses, "
      f"retypes, parks and checks the net sales again, got {after_check(1)}")
check(count("click", *QTY_POINT) == 1, f"the quantity is typed once, got {count('click', *QTY_POINT)}")
check(count("click", *REGISTER) == 1 and count("click", *CONFIRM) == 1,
      f"then Register and {m.CONFIRM_WORD} once each, got {count('click', *REGISTER)} and "
      f"{count('click', *CONFIRM)}")
check(f"retype 1/3: the net sales are no whole number of {WANT:,}; clearing the price box and typing "
      f"{WANT:,} again" in said, "the log says why it retypes")
check(f"typed {m.MAX_STACK}; the net sales make it 1" in said, "and goes on as before once the net sales agree")

out, error, said = run({1, 2, 3})
check(error is None and out is not None and out["price"] == WANT,
      f"the third retype is still allowed and lists, got {out!r} {error!r}")
check(typed() == [WANT, m.MAX_STACK, WANT, WANT, WANT], f"three retypes, got {typed()}")
check(S.checks == 4 and count("snap", "panel_will_not_confirm") == 3,
      f"four checks and a frame of each failed one, got {S.checks} and {count('snap', 'panel_will_not_confirm')}")

out, error, said = run({1, 2, 3, 4})
check(isinstance(error, m.Divergence) and out is None, f"a fourth failed check stops the listing, got {error!r}")
check(str(error) == f"the panel will not price {WANT:,} against its net sales after typing {m.MAX_STACK} and "
                   f"retyping the price 3 time(s). Nothing has been listed.",
      f"with the retypes named, got {error}")
check(typed() == [WANT, m.MAX_STACK, WANT, WANT, WANT], f"after three retypes, got {typed()}")
check(count("click", *REGISTER) == 0 and count("click", *CONFIRM) == 0, "and never clicks Register")
check(count("snap", "panel_will_not_confirm") == 4, "with a frame of every failed check")

out, error, said = run(())
check(error is None and out is not None and out["price"] == WANT, f"a price that checks first time lists, got {error!r}")
check(typed() == [WANT, m.MAX_STACK] and S.checks == 1,
      f"with no retype and one check, got {typed()} and {S.checks}")
check("retype" not in said and count("snap", "panel_will_not_confirm") == 0, "and says nothing about a retype")

(calibration.grab, m.panel_holds_item, calibration.slot_is_empty, calibration.ctrl_click,
 m.suggested_price, calibration.click, m.type_number, calibration.park, m.panel_quantity,
 m.check_price_field, m.find_button, m.underprice_warning, m.dialog_gone, calibration.occupied_slots,
 calibration.steps_table, m.time.sleep, calibration.snap) = saved

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
