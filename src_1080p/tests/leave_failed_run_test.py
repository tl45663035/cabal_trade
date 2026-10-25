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


inside = Image.open(HERE / "fixtures" / "in_dungeon_exit.png").convert("RGB")
fire = Image.open(HERE / "fixtures" / "in_dungeon_fire.png").convert("RGB")
field = Image.open(HERE / "fixtures" / "field_after_exit.png").convert("RGB")
check("inside TM with the Exit Dungeon prompt up reads as in a dungeon", dungeon.in_dungeon(inside))
check("inside TM with fire behind the buttons still reads as in a dungeon", dungeon.in_dungeon(fire))
check("Fort. Ruina after leaving reads as out of the dungeon", not dungeon.in_dungeon(field))


def fail_run(screens):
    shown, clicks, pressed = list(screens), [], []
    saved = (dungeon.calibration.grab, dungeon.click, dungeon.press_button, dungeon.dead,
             dungeon.calibration.close_everything, dungeon.calibration.snap, dungeon.FAILED_RUNS["in_a_row"],
             dungeon.dismiss_quest_failed)
    dungeon.dismiss_quest_failed = lambda *a, **k: False
    dungeon.calibration.grab = lambda *a, **k: shown.pop(0) if len(shown) > 1 else shown[0]
    dungeon.click = lambda x, y, *a, **k: clicks.append((x, y))
    dungeon.press_button = lambda name, *a, **k: pressed.append(name)
    dungeon.dead = lambda: False
    dungeon.calibration.close_everything = lambda *a, **k: None
    dungeon.calibration.snap = lambda *a, **k: None
    dungeon.FAILED_RUNS["in_a_row"] = 0
    error = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.survive_failed_run("tm", RuntimeError("still on the bike."))
    except RuntimeError as exc:
        error = str(exc)
    finally:
        (dungeon.calibration.grab, dungeon.click, dungeon.press_button, dungeon.dead,
         dungeon.calibration.close_everything, dungeon.calibration.snap, dungeon.FAILED_RUNS["in_a_row"],
         dungeon.dismiss_quest_failed) = saved
    return clicks, pressed, error


clicks, pressed, error = fail_run([field])
check("a failed run that is already out of the dungeon clicks nothing more", clicks == [] and pressed == [] and error is None,
      f"{clicks} {pressed} {error}")
clicks, pressed, error = fail_run([inside, field])
check("a failed run still inside TM clicks the dungeon's X, then Exit Dungeon, before the next run",
      clicks == [tuple(dungeon.LEAVE["exit"])] and pressed == ["leave_dungeon"] and error is None,
      f"{clicks} {pressed} {error}")
clicks, pressed, error = fail_run([inside])
check("if it is still inside after Exit Dungeon, the run stops, so no entry is bought from inside",
      error is not None and "no entry is bought" in error, str(error))

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
