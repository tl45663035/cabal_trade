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


fixture = lambda name: Image.open(HERE / "fixtures" / name).convert("RGB")
message = fixture("bike_message.png")
quiet = fixture("field_after_exit.png")
check("the game's 'cannot attack ... while riding a bike' line is read (IC1 #22, 03:46)", dungeon.bike_message(message))
check("a field frame with no such line is not", not dungeon.bike_message(quiet))
check("the room S ride is known to use the bike", dungeon.rides_bike(events("move_to_S")))
check("the room A walk is not", not dungeon.rides_bike(events("move_to_A")))


def get_off(screens):
    shown, pressed = list(screens), []
    saved = (dungeon.calibration.grab, dungeon.press, dungeon.tap, dungeon.hold_modifier, dungeon.time.sleep,
             dungeon.calibration.snap)

    def grab(*a, **k):
        return shown.pop(0) if len(shown) > 1 else shown[0]

    dungeon.calibration.grab = grab
    dungeon.press = lambda vk, *a, **k: pressed.append(("Alt", vk))
    dungeon.tap = lambda vk, *a, **k: pressed.append(vk)
    dungeon.hold_modifier = lambda down: None
    dungeon.time.sleep = lambda s: None
    dungeon.calibration.snap = lambda *a, **k: None
    error = got_off = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            got_off = dungeon.off_bike("before ic1")
    except RuntimeError as exc:
        error = str(exc)
    finally:
        (dungeon.calibration.grab, dungeon.press, dungeon.tap, dungeon.hold_modifier, dungeon.time.sleep,
         dungeon.calibration.snap) = saved
    return got_off, pressed, error


five, one = dungeon.KEYS["5"], dungeon.KEYS["1"]
got_off, pressed, error = get_off([quiet])
check("off the bike: 5 is pressed, no bike line comes, nothing else is pressed",
      got_off is False and pressed == [five] and error is None, f"{pressed} {error}")
got_off, pressed, error = get_off([quiet, message, quiet])
check("on the bike: 5 gets the bike line, so Alt+1 once, then 5 again gets nothing",
      got_off is True and pressed == [five, ("Alt", one), five] and error is None, f"{pressed} {error}")
got_off, pressed, error = get_off([quiet, message])
check("a bike that will not come off stops the run instead of spending it",
      error and "still on the bike" in error and pressed.count(("Alt", one)) == 1, f"{pressed} {error}")
check("the probe never presses Alt+1 when the bike line is not there", ("Alt", one) not in get_off([quiet])[1])

lit = fixture("on_bike_field_lit.png")
check("the field frame on the bike has a lit skill bar, which is why the bar is no longer the test",
      not dungeon.bike_message(lit))

source = (SRC / "dungeon.py").read_text(encoding="utf-8")
check("every run starts with the bike check, and the whole-run replay is followed by one",
      'off_bike(f"before {dungeon_name}"' in source and 'off_bike(f"after the {rest[0]} replay"' in source)
check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
