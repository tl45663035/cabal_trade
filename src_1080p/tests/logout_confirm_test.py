import contextlib
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["recovery.py", "--relog"]
import no_input
TRIPPED = no_input.arm()
import calibration
import open_inventory
import recovery

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


band = tuple(calibration._REG["dialog_buttons"])
popup = tuple(calibration._REG["popup"])
menu_at = (926, 552)
yes_at = (984, 638)
searched, clicks, pressed = [], [], []
state = {"confirmed": False}
real_wait_for = recovery._wait_for


def find_in(want, region_frac, whole=False, image=None):
    searched.append((want, tuple(region_frac)))
    return yes_at if tuple(region_frac) == band else None


def wait_for(want, *a, **k):
    if want == recovery.MENU_WORD:
        return menu_at
    return real_wait_for(want, *a, **k)


def click(x, y, *a, **k):
    clicks.append((x, y))
    if (x, y) == yes_at:
        state["confirmed"] = True


saved = (open_inventory.focus_game, open_inventory.press, recovery.account, recovery.character_list,
         recovery._wait_for, recovery._find_in, calibration.click, calibration.snap)
open_inventory.focus_game = lambda *a, **k: True
open_inventory.press = lambda *a, **k: pressed.append(a)
recovery.account = lambda *a, **k: {}
recovery.character_list = lambda *a, **k: ["character list"] if state["confirmed"] else None
recovery._wait_for = wait_for
recovery._find_in = find_in
calibration.click = click
calibration.snap = lambda *a, **k: None
try:
    said = io.StringIO()
    error = None
    with contextlib.redirect_stdout(said):
        try:
            done = recovery.logout(verbose=True)
        except recovery.Refused as exc:
            done, error = False, exc
    check("the logout reaches the character list when Yes reads only in the dialog's button band, as on cang at 14:43 "
          "over Bloody Ice", done is True, str(error) if error else said.getvalue().strip().splitlines()[-1])
    check("Yes is looked for in the dialog_buttons band, not across the whole popup region",
          [region for want, region in searched if want == recovery.YES_WORD] == [band] and popup != band,
          str(searched))
    check("the menu's Select Character is clicked, then the Yes the band found", clicks == [menu_at, yes_at], str(clicks))
finally:
    (open_inventory.focus_game, open_inventory.press, recovery.account, recovery.character_list,
     recovery._wait_for, recovery._find_in, calibration.click, calibration.snap) = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
