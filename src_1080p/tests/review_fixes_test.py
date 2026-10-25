import contextlib
import io
import os
import sys
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


@contextlib.contextmanager
def patched(*changes):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _value in changes]
    try:
        for obj, name, value in changes:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


source = (SRC / "dungeon.py").read_text(encoding="utf-8")
start = source.index("def run_dungeon(")
run = source[start:source.index("\ndef ", start + 1)]
check("the bike check before a run comes after OVER.clear(), so the last run's OVER does not skip it",
      run.index("OVER.clear()") < run.index('off_bike(f"before {dungeon_name}"'))
check("'Quest Dungeon Failed' is dismissed before before_exit (cancel_bm), not after, on both manual exits",
      run.count("box_up = dismiss_quest_failed(") == 2
      and all(run.index("box_up = dismiss_quest_failed(", start) < run.index('"before_exit"', run.index(
          "box_up = dismiss_quest_failed(", start))
              for start in (0, run.index("box_up = dismiss_quest_failed(") + 1)))

failed_box = Image.open(HERE / "fixtures" / "quest_dungeon_failed.jpg").convert("RGB")
clicks = []


def no_ok(name, *a, **k):
    raise RuntimeError("the quest_failed_ok window never read 'OK' within 2s")


with patched((dungeon.calibration, "grab", lambda *a, **k: failed_box), (dungeon, "press_button", no_ok),
             (dungeon, "click", lambda x, y, *a, **k: clicks.append((x, y))),
             (dungeon.calibration, "snap", lambda *a, **k: None)), contextlib.redirect_stdout(io.StringIO()):
    up = dungeon.dismiss_quest_failed()
check("an OK that does not read is clicked where it sits, not a crash that leaves the box up",
      up is True and clicks == [tuple(dungeon.centre(tuple(dungeon.BUTTONS["quest_failed_ok"]["window"])))], str(clicks))

counted = []
with patched((dungeon, "dead", lambda: True), (dungeon, "wait_for_respawn", lambda *a, **k: None),
             (dungeon, "count_failure", lambda name, why, verbose=True: counted.append(why)),
             (dungeon.calibration, "snap", lambda *a, **k: None)), contextlib.redirect_stdout(io.StringIO()):
    dungeon.survive_failed_run("eop", RuntimeError("boom"))
check("a death caught after an error counts toward the failures in a row, like a defeat screen does",
      len(counted) == 1 and "died" in counted[0], str(counted))


def hunt_nothing(stop_after, cast_seconds=0.0, over=False):
    asked = []

    def watch(name, words, stop, first, failed):
        first.set()
        while not stop.is_set():
            dungeon.LOCK["reads"] += 1
            stop.wait(0.02)

    def cast(*a, **k):
        if cast_seconds:
            time.sleep(cast_seconds)

    def off_bike(when, verbose=True):
        asked.append(when)
        if over:
            dungeon.OVER.set()
        return False

    error = None
    with patched((dungeon, "tap", lambda *a, **k: None), (dungeon, "focus_game", lambda: True),
                 (dungeon, "watch_target", watch), (dungeon, "off_bike", off_bike),
                 (dungeon, "aura_when_due", cast), (dungeon, "main_bm_when_due", lambda *a, **k: None),
                 (dungeon, "main_bm_recast_when_due", lambda *a, **k: None),
                 (dungeon, "bm_cycle_when_due", lambda *a, **k: None),
                 (dungeon, "aura_in_window", lambda *a, **k: None),
                 (dungeon, "NO_TARGET_STOP", stop_after)), contextlib.redirect_stdout(io.StringIO()):
        dungeon.OVER.clear()
        started = time.monotonic()
        try:
            dungeon.hunt("Zehgo of Ambition", ["zehgo"], lambda since: None, "kill_bm", None)
        except RuntimeError as exc:
            error = str(exc)
        took = time.monotonic() - started
        dungeon.OVER.clear()
    return asked, error, took


asked, error, took = hunt_nothing(0.5, cast_seconds=0.3)
check("time spent casting the aura and BM does not count toward the no-target limit",
      error and took >= 0.5 + 0.3, f"{took:.2f}s {error}")
asked, error, took = hunt_nothing(0.3, over=True)
check("a dungeon that ends while the bike is checked is left alone, not given up as a failure",
      error is None and len(asked) == 1, f"{asked} {error}")

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
