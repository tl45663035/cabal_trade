import contextlib
import io
import os
import sys
import threading
import time
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


def frame(name):
    return Image.open(HERE / "fixtures" / f"fire_archer_{name}.png").convert("RGB")


live, flicker, gone = frame("live"), frame("flicker"), frame("gone")
x, y = dungeon.TARGET_SLOTS[0][3]
check("Cang's frame column starts under the bar's top edge, whose colour the scene behind shows through",
      flicker.getpixel((x, y - 1)) != flicker.getpixel((x, y)) and not dungeon.near(
          flicker.getpixel((x, y - 1)), dungeon.TARGET_FRAME[0]), str(flicker.getpixel((x, y - 1))))
check("fire #23 room B: lava glow on the bar's top edge, Archer at half HP, reads as a live bar",
      dungeon.target_slots(flicker) == ([0], [0]), str(dungeon.target_slots(flicker)))
check("the Archer's bar a frame earlier reads as live", dungeon.target_slots(live) == ([0], [0]))
check("an emptied bar (0 HP) is up but not yellow, so the target is dead",
      dungeon.target_slots(gone) == ([0], []), str(dungeon.target_slots(gone)))
split = Image.open(HERE / "fixtures" / "tm_split_target.png").convert("RGB")
check("both halves of a split bar are read", dungeon.target_slots(split) == ([1, 2], [1, 2]),
      str(dungeon.target_slots(split)))
no_bar = split.copy()
no_bar.paste((40, 30, 20), (500, 20, 1420, 75))
check("with no bar on screen nothing is up", dungeon.target_slots(no_bar) == ([], []))


def watch(images, words, seconds):
    feed = iter(images)

    def grab(*a, **k):
        time.sleep(0.03)
        return next(feed, images[-1])

    stop, first, failed = threading.Event(), threading.Event(), {}
    saved = dungeon.calibration.grab, dungeon.calibration.snap
    dungeon.calibration.grab = grab
    dungeon.calibration.snap = lambda *a, **k: None
    dungeon.LOCK.update(boss=None, killed=False, watching=True, reads=0, hits=[], named=bool(words))
    dungeon.ENDED.clear()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            thread = threading.Thread(target=dungeon.watch_target,
                                      args=("Fire Guard Dark Archer", words, stop, first, failed))
            thread.start()
            thread.join(seconds)
            stop.set()
            thread.join()
        return dungeon.LOCK["killed"], failed
    finally:
        dungeon.calibration.grab, dungeon.calibration.snap = saved
        dungeon.LOCK.update(boss=None, killed=False, watching=False)
        dungeon.ENDED.clear()


archer = dungeon.target_words("fire", "Fire Guard Dark Archer")
check("the Archer's bar is read with HP left (the OCR model loads here, before the timed checks)",
      any(dungeon.hp_left(bar) for bar in dungeon.target_bars(live, archer)[1]))
killed, failed = watch([live, flicker] + [live] * 20, archer, 3.0)
check("the glowing frame does not count the named Archer dead", not killed and not failed, str(failed))
killed, failed = watch([live, gone], archer, 3.0)
check("an emptied bar still counts the named target dead", killed and not failed, str(failed))
killed, failed = watch([live, flicker] + [live] * 20, [], 3.0)
check("the glowing frame does not end a kill_all target either", not killed and not failed, str(failed))
killed, failed = watch([live, gone], [], 3.0)
check("a kill_all target whose bar empties is dead", killed and not failed, str(failed))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
