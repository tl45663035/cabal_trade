import contextlib
import io
import os
import sys
import tempfile
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


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_cancel_bm_"))
LIVE = dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None
ARENA = dungeon.DUNGEONS["chaos_arena"]
AFTER = ARENA["after_exit"]
MANUAL = ARENA["manual_exit"]
BEFORE = ARENA["before_exit"]
done = []
ending = {"overtime": False}
real_run_sequence = dungeon.run_sequence


def run_sequence(dungeon_name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
    if steps is None:
        dungeon.END["at"] = time.monotonic() - dungeon.END_WAIT
        dungeon.END["overtime"] = ending["overtime"]
        dungeon.OVER.set()
        dungeon.ENDED.set()
    else:
        done.append(("sequence", steps))


saved = (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
         dungeon.focus_game, dungeon.off_for_the_day, dungeon.STATE,
         calibration.snap)
dungeon.STATE = SCRATCH / "dungeon_next.json"
dungeon.run_sequence = run_sequence
dungeon.right_click = lambda x, y, settle: done.append(("rightclick", x, y))
dungeon.exit_dungeon = lambda *a, **k: done.append(("exit",))
dungeon.focus_game = lambda *a, **k: True
dungeon.off_for_the_day = lambda verbose=True: done.append(("off",))
calibration.snap = lambda *a, **k: None
try:
    world = tuple(calibration.load_shared()["world_boss"]["bm_icon"])
    check(dungeon.BM_ICON == world == (254, 148),
          f"the dungeon cancels BM on the world boss's BM icon, got {dungeon.BM_ICON} and {world}")
    check(BEFORE == ["cancel_bm"] and dungeon.cancel_bm_clicks("chaos_arena") == 1
          and dungeon.cancel_bm_clicks("fire") == dungeon.CANCEL_BM_CLICKS == 3,
          f"a Chaos Arena cancels BM with one right-click, the fire dungeon with its own "
          f"{dungeon.CANCEL_BM_CLICKS}, got {BEFORE} and "
          f"{dungeon.cancel_bm_clicks('chaos_arena')}/{dungeon.cancel_bm_clicks('fire')}")

    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.run_dungeon("chaos_arena", "CA3")
    check(done == [("sequence", BEFORE), ("exit",), ("sequence", AFTER)],
          f"a cleared arena cancels BM, then exits, then runs after_exit, got {done}")
    check("chaos_arena CA3: the dungeon is over" in said.getvalue(),
          f"the log names the arena's end: {said.getvalue()!r}")

    for name, location, clicks in (("chaos_arena", "CA3", 1), ("fire", "Fire", 3)):
        done.clear()
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            real_run_sequence(name, location, {}, True, ["cancel_bm"], False)
        check(done == [("rightclick", 254, 148)] * clicks
              and f"BM icon right-clicked {clicks} times at [254, 148]" in said.getvalue(),
              f"{name}'s cancel_bm step right-clicks the BM icon {clicks} time(s), got {done}: "
              f"{said.getvalue()!r}")

    done.clear()
    ending["overtime"] = True
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.run_dungeon("chaos_arena", "CA2")
    check(done == [("sequence", MANUAL), ("off",)],
          f"an arena past its time limit still leaves by hand only, got {done}")

    done.clear()
    dungeon.focus_game = lambda *a, **k: False
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            real_run_sequence("chaos_arena", "CA3", {}, True, ["cancel_bm"], False)
        raised = False
    except RuntimeError:
        raised = True
    check(raised and done == [],
          f"with the game not in front nothing is right-clicked, got {raised} and {done}")
finally:
    (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
     dungeon.focus_game, dungeon.off_for_the_day, dungeon.STATE,
     calibration.snap) = saved
    dungeon.OVER.clear()
    dungeon.ENDED.clear()

check((dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None) == LIVE,
      "the live dungeon_next.json is left alone")
check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
