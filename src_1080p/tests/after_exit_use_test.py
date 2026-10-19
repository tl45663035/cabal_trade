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
gate = ARENA["sequence"].index(["kill", "Gate of Chaos"])
check(ARENA["sequence"][gate + 1:gate + 3] == [["timer", 330], ["tap", "="]],
      f"= is pressed once the first gate is broken, got {ARENA['sequence'][gate + 1:gate + 3]}")
check(ARENA["after_exit"] == [["wait", 3], "after_exit", ["tap", "="], ["use", "A+2"]],
      f"after the exit walk = is pressed and A+2 used, got {ARENA['after_exit']}")
check("bm_pot" not in dungeon.BM_LOOP, f"no A- in the BM loop, got {dungeon.BM_LOOP}")

done = []
saved = (dungeon.tap, dungeon.right_click, dungeon.focus_game, calibration.snap)
dungeon.tap = lambda vk, hold=None: done.append(("tap", vk))
dungeon.right_click = lambda x, y, settle: done.append(("rightclick", (x, y)))
dungeon.focus_game = lambda *a, **k: True
calibration.snap = lambda *a, **k: None
try:
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.run_sequence("chaos_arena", "CA6", {}, True, ARENA["after_exit"][2:], False)
    check(done == [("tap", dungeon.KEYS["="]), ("rightclick", dungeon.SLOTS["A+2"])],
          f"= is pressed, then A+2 right-clicked, once each, got {done}")
    check("tap =" in said.getvalue() and "right-click A+2 at [737, 940]" in said.getvalue(),
          f"the log names both, got {said.getvalue()!r}")
finally:
    dungeon.tap, dungeon.right_click, dungeon.focus_game, calibration.snap = saved

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
