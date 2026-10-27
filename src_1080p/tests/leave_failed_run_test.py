import contextlib
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "cang"
os.chdir(SRC)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC))
sys.argv = ["dungeon.py", "--config", "cang"]
import no_input
TRIPPED = no_input.arm()
from PIL import Image
import dungeon

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


field = Image.open(HERE / "fixtures" / "field_after_exit.png").convert("RGB")

failed_box = Image.open(HERE / "fixtures" / "quest_dungeon_failed.jpg").convert("RGB")
pressed = []
saved = (dungeon.calibration.grab, dungeon.press_button, dungeon.calibration.snap)
dungeon.calibration.grab = lambda *a, **k: failed_box
dungeon.press_button = lambda name, *a, **k: pressed.append(name)
dungeon.calibration.snap = lambda *a, **k: None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        up = dungeon.dismiss_quest_failed()
finally:
    dungeon.calibration.grab, dungeon.press_button, dungeon.calibration.snap = saved
check("'Quest Dungeon Failed' (EOP #11, 5 min up) is read and its OK pressed before anything else",
      up and pressed == ["quest_failed_ok"], f"{up} {pressed}")
dungeon.calibration.grab = lambda *a, **k: field
try:
    check("with no such box up, nothing is pressed", dungeon.dismiss_quest_failed(False) is False)
finally:
    dungeon.calibration.grab = saved[0]

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
