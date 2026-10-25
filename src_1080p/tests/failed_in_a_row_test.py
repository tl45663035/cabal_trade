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
import dungeon

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def play(outcomes):
    results = list(outcomes)

    def run_dungeon(name, location, verbose=True):
        dungeon.RUN_OUTCOME["failed"] = results.pop(0) == "timeout"

    noop = lambda *a, **k: None
    names = ["dungeon_report_due", "burst_over", "world_boss_turn", "revive_if_dead", "pre_run_buys",
             "powerful_pot_due", "start_block", "cool_down", "dead", "run_dungeon"]
    saved = {name: getattr(dungeon, name) for name in names}
    saved_burst, saved_state = dungeon.BURST, dict(dungeon.FAILED_RUNS)
    for name in names:
        setattr(dungeon, name, noop)
    dungeon.burst_over = lambda: False
    dungeon.world_boss_turn = lambda *a, **k: False
    dungeon.dead = lambda: False
    dungeon.run_dungeon = run_dungeon
    dungeon.BURST = None
    dungeon.FAILED_RUNS["in_a_row"] = 0
    played, error = 0, None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            while results:
                dungeon.run_in_turn("ic1", "IC1", None, True)
                played += 1
    except RuntimeError as exc:
        error = str(exc)
    finally:
        for name, value in saved.items():
            setattr(dungeon, name, value)
        dungeon.BURST = saved_burst
        dungeon.FAILED_RUNS.update(saved_state)
    return played, error


most = int(dungeon.DEATH["failed_in_a_row"])
played, error = play(["timeout"] * 26)
check(f"runs that time out count as failures: the run stops at the {most}th in a row, not after 26",
      played == most - 1 and error and f"{most} runs in a row failed" in error, f"{played} {error}")
played, error = play(["timeout"] * (most - 1) + ["pass"] + ["timeout"] * (most - 1))
check("a pass in between starts the count again", error is None and played == 2 * most - 1, f"{played} {error}")


def hunt_with_nothing(bike_answers):
    answers, asked = list(bike_answers), []

    def watch(name, words, stop, first, failed):
        first.set()
        while not stop.is_set():
            dungeon.LOCK["reads"] += 1
            stop.wait(0.02)

    def off_bike(when, verbose=True):
        asked.append(when)
        return answers.pop(0) if answers else False

    saved = (dungeon.tap, dungeon.focus_game, dungeon.watch_target, dungeon.off_bike, dungeon.NO_TARGET_STOP,
             dungeon.bm_cycle_when_due)
    dungeon.tap = lambda *a, **k: None
    dungeon.focus_game = lambda: True
    dungeon.watch_target = watch
    dungeon.off_bike = off_bike
    dungeon.bm_cycle_when_due = lambda *a, **k: None
    dungeon.NO_TARGET_STOP = 0.3
    error = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            dungeon.hunt("Divine Dazzled Researcher", ["researcher"], lambda since: None, "kill_bm", None)
    except RuntimeError as exc:
        error = str(exc)
    finally:
        (dungeon.tap, dungeon.focus_game, dungeon.watch_target, dungeon.off_bike, dungeon.NO_TARGET_STOP,
         dungeon.bm_cycle_when_due) = saved
    return asked, error


asked, error = hunt_with_nothing([False])
check("a named kill with nothing targeted checks the bike, then gives the run up instead of waiting out the limit",
      len(asked) == 1 and error and "nothing targeted" in error, f"{asked} {error}")
asked, error = hunt_with_nothing([True, False])
check("if the bike was the reason, it gets off and the hunt goes on before giving up",
      len(asked) == 2 and error and "nothing targeted" in error, f"{asked} {error}")
check("cang gives up after 30s with nothing targeted", dungeon.NO_TARGET_STOP == 30.0, str(dungeon.NO_TARGET_STOP))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
