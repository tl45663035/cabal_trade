import contextlib
import io
import json
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


def events(name):
    lines = (SRC / "dungeon" / "cang" / "tm" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines[1:] if line.strip()]


on = Image.open(HERE / "fixtures" / "on_bike.jpg").convert("RGB")
off = Image.open(HERE / "fixtures" / "off_bike.jpg").convert("RGB")
dim = dungeon.BIKE["dim_below"]
check("the greyed skill bar on the bike reads under the line",
      dungeon.bike_brightness(on) < dim, f"{dungeon.bike_brightness(on):.0f}")
check("the lit skill bar off the bike reads over it",
      dungeon.bike_brightness(off) >= dim, f"{dungeon.bike_brightness(off):.0f}")
check("the room S ride is known to use the bike", dungeon.rides_bike(events("move_to_S")))
check("the room A walk is not", not dungeon.rides_bike(events("move_to_A")))


def get_off(frames):
    shown, pressed = list(frames), []
    saved = (dungeon.calibration.grab, dungeon.press, dungeon.hold_modifier, dungeon.time.sleep,
             dungeon.calibration.snap)
    dungeon.calibration.grab = lambda *a, **k: shown.pop(0) if len(shown) > 1 else shown[0]
    dungeon.press = pressed.append
    dungeon.hold_modifier = lambda down: None
    dungeon.time.sleep = lambda s: None
    dungeon.calibration.snap = lambda *a, **k: None
    error = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.off_bike("after move_to_S")
    except RuntimeError as exc:
        error = str(exc)
    finally:
        (dungeon.calibration.grab, dungeon.press, dungeon.hold_modifier, dungeon.time.sleep,
         dungeon.calibration.snap) = saved
    return pressed, error


cooling = Image.open(HERE / "fixtures" / "buffs_cooling_off_bike.png").convert("RGB")
check("off the bike with the boss buffs on cooldown (tm #25, before room S) does not read as on the bike",
      dungeon.bike_brightness(cooling) >= dim, f"{dungeon.bike_brightness(cooling):.0f}")
check("the room S ride is only checked after it, not before",
      "off_bike(f\"before" not in Path(dungeon.__file__).read_text(encoding="utf-8"))

one = dungeon.KEYS["1"]
pressed, error = get_off([off])
check("off the bike after the ride: nothing is pressed", pressed == [] and error is None, f"{pressed} {error}")
pressed, error = get_off([on, off])
check("still on the bike after the ride: Alt+1 once, then it reads off", pressed == [one] and error is None,
      f"{pressed} {error}")
pressed, error = get_off([on])
check("a bike that still reads on gets no second Alt+1, which would mount it again, and the run goes on",
      pressed == [one] and error is None, f"{pressed} {error}")

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
