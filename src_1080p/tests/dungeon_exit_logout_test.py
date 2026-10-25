import contextlib
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "cang"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["recovery.py", "--relog"]
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import calibration
import open_inventory
import recovery

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


frame = Image.open(HERE / "fixtures" / "dungeon_exit_yes.jpg").convert("RGB")
check("the Yes of 'Exit Game in a Dungeon' reads in its band on the frame from tm #17",
      recovery._find_in(recovery.YES_WORD, recovery.DUNGEON_EXIT_YES_F, whole=True, image=frame) is not None)
check("and not in the normal logout band, which is why the relog stopped there",
      recovery._find_in(recovery.YES_WORD, calibration.DIALOG_BUTTONS_F, whole=True, image=frame) is None)

menu_at, clicks, state = (926, 552), [], {"confirmed": False}


def wait_for(want, timeout=None, whole=True, verbose=True, region=None):
    if want == recovery.MENU_WORD:
        return menu_at
    return recovery._find_in(want, region, whole=whole, image=frame)


def click(x, y, *a, **k):
    clicks.append((x, y))
    if (x, y) != menu_at:
        state["confirmed"] = True


saved = (open_inventory.focus_game, open_inventory.press, recovery.account, recovery.character_list,
         recovery._wait_for, calibration.click, calibration.snap)
open_inventory.focus_game = lambda *a, **k: True
open_inventory.press = lambda *a, **k: None
recovery.account = lambda *a, **k: {}
recovery.character_list = lambda *a, **k: ["character list"] if state["confirmed"] else None
recovery._wait_for = wait_for
calibration.click = click
calibration.snap = lambda *a, **k: None
try:
    error = None
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            done = recovery.logout(verbose=True)
        except recovery.Refused as exc:
            done, error = False, exc
    check("a logout from inside a dungeon clicks that Yes and reaches the character list",
          done is True and len(clicks) == 2 and abs(clicks[1][1] - 614) < 10, f"{clicks} {error}")
finally:
    (open_inventory.focus_game, open_inventory.press, recovery.account, recovery.character_list,
     recovery._wait_for, calibration.click, calibration.snap) = saved

check("no real input reached the game", TRIPPED == [], str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
