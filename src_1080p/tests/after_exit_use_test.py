import contextlib
import io
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["dungeon.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import dungeon

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


ARENA = dungeon.DUNGEONS["chaos_arena"]
check(not any(isinstance(step, list) and step[0] == "use" for step in ARENA["sequence"]),
      f"the arena sequence uses no A+ slot any more, got {ARENA['sequence']}")
check(ARENA["after_exit"][-1] == ["spam", "A+1", "A+2", 15],
      f"after the exit walk A+1 and A+2 are used for 15s, got {ARENA['after_exit']}")
check(dungeon.LOOP_GAP == 0.5, f"0.5s between uses, got {dungeon.LOOP_GAP}")

clicks = []
started = {"at": None}
saved = (dungeon.right_click, dungeon.focus_game, calibration.snap)
dungeon.right_click = lambda x, y, settle: clicks.append(
    ((x, y), round(time.monotonic() - started["at"], 2)))
dungeon.focus_game = lambda *a, **k: True
calibration.snap = lambda *a, **k: None
try:
    dungeon.AURA_DUE["at"] = None
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        started["at"] = time.monotonic()
        dungeon.run_sequence("chaos_arena", "CA5", {}, True, [["spam", "A+1", "A+2", 2]], False)
    points = [point for point, _at in clicks]
    times = [at for _point, at in clicks]
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    check(points[:4] == [dungeon.SLOTS["A+1"], dungeon.SLOTS["A+2"]] * 2 and len(points) == 4,
          f"A+1 and A+2 are right-clicked in turn, got {clicks}")
    check(gaps and all(0.45 <= gap <= 0.6 for gap in gaps) and times[0] < 0.1,
          f"one use every 0.5s from the start, got {times}")
    check(dungeon.AURA_DUE["at"] is None,
          f"using A+ slots starts no aura timer, got {dungeon.AURA_DUE}")
    check("A+1 -> A+2 right-clicked 0.5s apart, over and over for 2s" in said.getvalue(),
          f"the log names the uses, got {said.getvalue()!r}")
finally:
    dungeon.right_click, dungeon.focus_game, calibration.snap = saved
    dungeon.AURA_DUE["at"] = None

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
