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


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_boss_again_"))
LIVE = dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None
MANUAL = dungeon.DUNGEONS["chaos_arena"]["manual_exit"]
AFTER = dungeon.DUNGEONS["chaos_arena"]["after_exit"]
BOSS, WORDS = "Chaotic Orca", ["orca"]
MISREAD, LOCKED = "Lv.iysLnaoticUrca", "Lv.195ChaoticOrca"
TARGET = dungeon.KEYS[dungeon.TARGET_KEY]
done, taps, kills, reads = [], [], [], {"script": [], "count": 0}
plan = {"kill_bm": True, "ends": True}


def run_sequence(dungeon_name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
    if steps is not None:
        done.append(("sequence", steps))
        return
    phase["killing"].set()
    if plan["kill_bm"]:
        dungeon.LAST_KILL.update(name=BOSS, words=WORDS, loop=dungeon.BM_LOOP)


def read_crops(crops):
    reads["count"] += 1
    script = reads["script"]
    return [script.pop(0) if script else "Lv.150ChaoticDangoHela"]


def kill_bm(name, words, verbose=True, loop=None):
    kills.append((name, words, loop, time.monotonic()))
    if plan["ends"]:
        dungeon.END["at"] = time.monotonic() - dungeon.END_WAIT
        dungeon.OVER.set()
        dungeon.ENDED.set()
    return True


def arena(script, kill_bm_ran=True, ends=True, where=("chaos_arena", "CA5")):
    done.clear()
    taps.clear()
    kills.clear()
    reads.update(script=list(script), count=0)
    plan.update(kill_bm=kill_bm_ran, ends=ends)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.run_dungeon(*where)
    left = dict(dungeon.LAST_KILL)
    dungeon.OVER.clear()
    dungeon.ENDED.clear()
    return said.getvalue(), left


saved = (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
         dungeon.focus_game, dungeon.off_for_the_day, dungeon.read_crops,
         dungeon.white_text, dungeon.tap, dungeon.aura_when_due, dungeon.kill_bm,
         dungeon.END_TIMEOUT, dungeon.TARGET_EVERY, dungeon.STATE,
         dungeon.server_day, calibration.grab, calibration.snap)
real_kill_bm = dungeon.kill_bm
dungeon.STATE = SCRATCH / "dungeon_next.json"
dungeon.run_sequence = run_sequence
dungeon.right_click = lambda x, y, settle: done.append(("rightclick", x, y))
dungeon.exit_dungeon = lambda *a, **k: done.append(("exit",))
dungeon.focus_game = lambda *a, **k: True
dungeon.off_for_the_day = lambda verbose=True: done.append(("off",))
dungeon.read_crops = read_crops
dungeon.white_text = lambda image, box: None
dungeon.tap = lambda vk, hold=None: taps.append(vk)
dungeon.aura_when_due = lambda verbose=True: None
dungeon.kill_bm = kill_bm
dungeon.TARGET_EVERY = 0.01
dungeon.server_day = lambda: "2026-10-19"
calibration.grab = lambda *a, **k: None
calibration.snap = lambda *a, **k: None
try:
    check(dungeon.DUNGEONS["chaos_arena"].get("kill_again") is True
          and not dungeon.DUNGEONS["fire"].get("kill_again"),
          "the boss is killed again in a Chaos Arena only")
    dungeon.END_TIMEOUT = 5.0
    said, left = arena([MISREAD, LOCKED])
    check([kill[:3] for kill in kills] == [(BOSS, WORDS, dungeon.BM_LOOP)] and taps == [TARGET],
          f"after one misread the boss on the bar is killed again, got {kills} after {taps}")
    check(f"the HP bar reads {LOCKED!r}: {BOSS} is targeted again (orca), so kill_bm again" in said
          and "is killed again whenever the HP bar shows it" in said,
          f"the log says the boss is killed again, got {said!r}")
    check(done[1:] == [("exit",), ("sequence", AFTER)],
          f"a kill again that clears the arena exits as cleared, got {done}")

    said, left = arena([LOCKED])
    check(len(kills) == 1 and taps == [],
          f"a boss still on the bar is killed again before any target press, got {kills} after {taps}")

    dungeon.END_TIMEOUT = 0.3
    said, left = arena([MISREAD, LOCKED], ends=False)
    gap = time.monotonic() - kills[0][3] if kills else None
    check(len(kills) == 1 and done == [("sequence", MANUAL)]
          and gap is not None and gap >= dungeon.END_TIMEOUT,
          f"after a kill again the end screen gets its full wait again, got {kills} "
          f"{done} after {gap}s")

    said, left = arena([])
    check(kills == [] and done == [("sequence", MANUAL)] and len(taps) > 1,
          f"with no boss on the bar the old wait and manual exit are kept, got {kills} {done}")
    check(left == {"name": None, "words": None, "loop": None},
          f"the boss to kill again is forgotten when the arena ends, got {left}")

    dungeon.END_TIMEOUT = 0.3
    said, left = arena([LOCKED], where=("fire", "Fire"))
    check(kills == [] and reads["count"] == 0 and "killed again" not in said
          and done == [("sequence", dungeon.manual_exit("fire"))],
          f"the fire dungeon keeps its old end-screen wait, got {kills}, {reads}, {done}")

    said, left = arena([LOCKED], kill_bm_ran=False)
    check(kills == [] and reads["count"] == 0 and "killed again" not in said,
          f"with no kill_bm in the sequence the bar is not read, got {kills}, {reads}")

    dungeon.kill_bm = real_kill_bm
    hunt = dungeon.hunt
    dungeon.hunt = lambda *a, **k: True
    try:
        dungeon.kill_bm(BOSS, WORDS, False)
        check(dungeon.LAST_KILL == {"name": BOSS, "words": WORDS, "loop": dungeon.BM_LOOP},
              f"kill_bm keeps the boss it kills, got {dungeon.LAST_KILL}")
    finally:
        dungeon.hunt = hunt
        dungeon.LAST_KILL.update(name=None, words=None, loop=None)
finally:
    (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
     dungeon.focus_game, dungeon.off_for_the_day, dungeon.read_crops,
     dungeon.white_text, dungeon.tap, dungeon.aura_when_due, dungeon.kill_bm,
     dungeon.END_TIMEOUT, dungeon.TARGET_EVERY, dungeon.STATE,
     dungeon.server_day, calibration.grab, calibration.snap) = saved
    dungeon.OVER.clear()
    dungeon.ENDED.clear()

check(dungeon.STATE.name == "dungeon_next.json"
      and (dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None) == LIVE,
      "the live dungeon_next.json is left alone")
check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
