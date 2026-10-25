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
on_frames = ["on_bike.jpg", "bike_message.png", "on_bike_field_lit.png", "in_dungeon_exit.png",
             "on_bike_after_leave.png"]
off_frames = ["off_bike.jpg", "field_after_exit.png", "buffs_cooling_off_bike.png", "quest_dungeon_failed.jpg"]
for name in on_frames:
    check(f"on the bike, hotbar slot 1 reads greyed out ({name})", dungeon.greyed(fixture(name)),
          f"{dungeon.bike_slot(fixture(name)):.0f}")
for name in off_frames:
    check(f"off the bike, hotbar slot 1 reads lit ({name})", not dungeon.greyed(fixture(name)),
          f"{dungeon.bike_slot(fixture(name)):.0f}")
check("the room S ride is known to use the bike", dungeon.rides_bike(events("move_to_S")))
check("the room A walk is not", not dungeon.rides_bike(events("move_to_A")))
on, off = fixture("bike_message.png"), fixture("field_after_exit.png")


def get_off(screens):
    shown, pressed = list(screens), []
    saved = (dungeon.calibration.grab, dungeon.press, dungeon.click, dungeon.hold_modifier, dungeon.time.sleep,
             dungeon.calibration.snap)

    def grab(*a, **k):
        return shown.pop(0) if len(shown) > 1 else shown[0]

    dungeon.calibration.grab = grab
    dungeon.press = lambda vk, *a, **k: pressed.append(("Alt", vk))
    dungeon.click = lambda *a, **k: None
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
        (dungeon.calibration.grab, dungeon.press, dungeon.click, dungeon.hold_modifier, dungeon.time.sleep,
         dungeon.calibration.snap) = saved
    return got_off, pressed, error


one = dungeon.KEYS["1"]
got_off, pressed, error = get_off([off])
check("off the bike: nothing is pressed", got_off is False and pressed == [] and error is None, f"{pressed} {error}")
got_off, pressed, error = get_off([on, off])
check("a slot 1 greyed only for a moment (the dash cooling down) is not taken for the bike",
      got_off is False and pressed == [], f"{pressed} {error}")
got_off, pressed, error = get_off([on, on, on, off])
check("on the bike (greyed on both reads): Alt+1 once, then slot 1 is lit again",
      got_off is True and pressed == [("Alt", one)] and error is None, f"{pressed} {error}")
got_off, pressed, error = get_off([on])
check("a bike that will not come off stops the run, with Alt+1 pressed only once",
      error and "still on the bike" in error and pressed == [("Alt", one)], f"{pressed} {error}")

source = (SRC / "dungeon.py").read_text(encoding="utf-8")
check("every run starts with the bike check, and the whole-run replay is followed by one",
      'off_bike(f"before {dungeon_name}"' in source and 'off_bike(f"after the {rest[0]} replay"' in source)
check("the disenchant does not check the bike (the extraction works on it)",
      'off_bike("before the disenchant' not in source)
dungeon.OVER.set()
try:
    got_off, pressed, error = get_off([on, on, on, off])
finally:
    dungeon.OVER.clear()
check("after the dungeon is over (EOP #15 got on the bike leaving it) the next run's check still runs and gets off",
      got_off is True and pressed == [("Alt", one)], f"{pressed} {error}")

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
