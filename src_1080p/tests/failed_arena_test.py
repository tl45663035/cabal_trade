import contextlib
import io
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.environ["CABAL_SALES_DB"] = str(Path(tempfile.mkdtemp(prefix="cabal_failed_arena_db_")) / "sales.db")
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["driver.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import driver
import dungeon

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_failed_arena_"))
RUN = SCRATCH / "debug_frames" / "2026-10-21_170000_run"
KEPT = SCRATCH / "failed_dungeons"
LIVE = dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None
LIVE_KEPT = (sorted(p.name for p in calibration.FAILED_DIR.iterdir())
             if calibration.FAILED_DIR.is_dir() else None)
SHARED = json.loads((SRC / "config.json").read_text(encoding="utf-8"))
KEEP = int(calibration.load_shared()["debug"]["keep_failed_dungeons"])
SEQUENCE = dungeon.DUNGEONS["chaos_arena"]["sequence"]
ENTRY = SEQUENCE[:dungeon.step_index(SEQUENCE, "wait_timer")]
DAY = "2026-10-21"
done = []
ending = {"kind": "overtime"}


def take(label, count=1):
    for _ in range(count):
        calibration._FRAME_N += 1
        n = calibration._FRAME_N
        shot = calibration.RUN_FRAMES / f"{n:05d}_{label}.png"
        shot.write_bytes(f"frame {n}".encode())
        with open(calibration.RUN_FRAMES / calibration.FRAME_INDEX, "a",
                  encoding="utf-8") as index:
            index.write(json.dumps({"n": n, "file": shot.name}) + chr(10))


def run_sequence(dungeon_name, location, phase, verbose=True, steps=None,
                 stop_when_over=True, timer=None):
    if timer is None:
        done.append(("sequence", steps))
        take("exit")
        return
    if steps == ENTRY:
        take("entry", 4)
        timer["until"] = time.monotonic() + 300
        return
    take("boss", 5)
    if ending["kind"] == "none":
        phase["killing"].set()
        return
    dungeon.END["at"] = time.monotonic() - dungeon.END_WAIT
    dungeon.END["overtime"] = ending["kind"] == "overtime"
    dungeon.END["defeated"] = ending["kind"] == "defeated"
    dungeon.OVER.set()
    dungeon.ENDED.set()


def write(state):
    dungeon.STATE.write_text(json.dumps(state), encoding="utf-8")


def read():
    return json.loads(dungeon.STATE.read_text(encoding="utf-8"))


def folders():
    return sorted(p for p in KEPT.iterdir()) if KEPT.is_dir() else []


def numbers(folder, pattern="*.png"):
    return sorted(int(p.name.partition("_")[0]) for p in folder.glob(pattern))


def arena(kind, location):
    done.clear()
    ending["kind"] = kind
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.run_dungeon("chaos_arena", location)
    dungeon.OVER.clear()
    dungeon.ENDED.clear()
    return said.getvalue()


saved = (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
         dungeon.focus_game, dungeon.press_buttons, dungeon.internet_now,
         dungeon.END_TIMEOUT, dungeon.STATE, calibration.snap,
         calibration.RUN_FRAMES, calibration.FAILED_DIR, calibration.FRAMES_ON,
         calibration._FRAME_N, dict(dungeon.ACTIVE))
RUN.mkdir(parents=True)
dungeon.STATE = SCRATCH / "dungeon_next.json"
dungeon.run_sequence = run_sequence
dungeon.right_click = lambda x, y, settle: done.append(("rightclick", x, y))
dungeon.exit_dungeon = lambda *a, **k: done.append(("exit",)) or take("exit")
dungeon.focus_game = lambda *a, **k: True
dungeon.press_buttons = lambda label, steps, verbose=True: done.append(("buttons", label))
dungeon.internet_now = lambda: None
dungeon.END_TIMEOUT = 0.0
calibration.snap = lambda label, image=None, alongside=False: take(label)
calibration.RUN_FRAMES, calibration.FAILED_DIR = RUN, KEPT
calibration.FRAMES_ON, calibration._FRAME_N = True, 0
try:
    check(SHARED["debug"]["keep_failed_dungeons"] == KEEP > 0,
          f"config.json keeps the newest failed dungeons' frames, got {KEEP}")
    write({"server_dated": True, "ran": {"5": 1}, "successful": {}, "day": DAY, "last": 5})
    with contextlib.redirect_stdout(io.StringIO()):
        due = dungeon.start(5, rotate=True)
    check(due is not None and dungeon.ACTIVE["frames"] == [(1, 4)],
          f"the arena's first half hands its frames on to the boss half, got {dungeon.ACTIVE}")
    take("trading", 6)

    calls = []
    stubs = {"resume": lambda: None, "dungeon_open": lambda: True,
             "server_day": lambda: DAY, "war_hold": lambda: None,
             "next_level": lambda today: calls.append(("next", today)) or 4,
             "start": lambda level, verbose=True, rotate=False:
                 calls.append(("start", level)) or time.monotonic() + 300}
    put_back = {name: getattr(dungeon, name) for name in stubs}
    saved_on, saved_state = driver.dungeon_on, dict(driver._DUNGEON)
    for name, stub in stubs.items():
        setattr(dungeon, name, stub)
    driver.dungeon_on = lambda: True
    try:
        driver._DUNGEON.update(due=time.monotonic() - 1, spent=False, off=False,
                               resume_checked=True, closed=False, war=None)
        ending["kind"] = "overtime"
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            acted = driver.dungeon_turn()
        said = said.getvalue()
    finally:
        for name, value in put_back.items():
            setattr(dungeon, name, value)
        driver.dungeon_on = saved_on
        driver._DUNGEON.clear()
        driver._DUNGEON.update(saved_state)
        dungeon.OVER.clear()
        dungeon.ENDED.clear()
    check(acted is True and calls == [("next", DAY), ("start", 4)],
          f"after an arena past its time limit the next CA in the list starts, got {acted}, {calls}")
    check("off_day" not in read() and "ran past its time limit" not in said
          and "no more dungeons" not in said,
          f"an arena past its time limit takes no day off, got {read()}: {said!r}")
    check("chaos_arena CA5: past its 600s; left it by hand" in said
          and "level 4 key" in said,
          f"the log says the manual exit, then the next level, got {said!r}")

    made = folders()
    want = [1, 2, 3, 4] + list(range(11, 19))
    check(len(made) == 1 and made[0].name.endswith(f"_CA5_{RUN.name}")
          and numbers(made[0]) == want,
          f"both halves of the failed arena and its exit are kept, not the trading in between, "
          f"got {[p.name for p in made]} {numbers(made[0]) if made else None}")
    index = [json.loads(line)["n"] for line in
             (made[0] / calibration.FRAME_INDEX).read_text(encoding="utf-8").splitlines()] if made else []
    check(index == want, f"the kept folder's frame index lists just the kept frames, got {index}")
    check(f"kept {len(want)} frame(s) of the failed CA5 in {made[0] if made else None}" in said,
          f"the log says where the frames went, got {said!r}")
    links = [os.stat(p).st_nlink for p in made[0].glob("*.png")] if made else []
    check(links and all(count == 2 for count in links),
          f"the kept frames are links to the run's own, so they cost no disk until the run prunes, got {links}")
    for shot in RUN.glob("*.png"):
        shot.unlink()
    survived = [p.read_bytes() for p in sorted(made[0].glob("*.png"))] if made else []
    check(survived == [f"frame {n}".encode() for n in want],
          f"the kept frames outlive the run's pruning, got {survived}")

    write({"server_dated": True, "ran": {"3": 1, "2": 1}, "successful": {}, "day": DAY, "last": 3})
    before = len(folders())
    first = calibration._FRAME_N + 1
    said = arena("defeated", "CA3")
    made = [p for p in folders() if "_CA3_" in p.name]
    check(len(folders()) == before + 1 and len(made) == 1
          and numbers(made[0]) == list(range(first, calibration._FRAME_N + 1)),
          f"a defeated arena keeps its frames, got {[p.name for p in folders()]}: {said!r}")
    first = calibration._FRAME_N + 1
    said = arena("none", "CA2")
    made = [p for p in folders() if "_CA2_" in p.name]
    check(len(made) == 1 and numbers(made[0]) == list(range(first, calibration._FRAME_N + 1)),
          f"an arena left by hand with no end screen keeps its frames, got "
          f"{[p.name for p in folders()]}: {said!r}")
    before = folders()
    said = arena("cleared", "CA1")
    check(folders() == before and "kept" not in said,
          f"a cleared arena keeps nothing, got {[p.name for p in folders()]}: {said!r}")

    calibration.FRAMES_ON = False
    before = folders()
    said = arena("overtime", "CA4")
    check(folders() == before and "kept" not in said,
          f"with frames off nothing is kept, got {[p.name for p in folders()]}: {said!r}")
    calibration.FRAMES_ON = True

    old = [KEPT / f"2000-01-0{day}_000000_CA1_2000-01-01_000000_run" for day in range(1, KEEP + 1)]
    for folder in old:
        folder.mkdir()
    spent = len(folders()) + 1 - KEEP
    said = arena("overtime", "CA6")
    left = folders()
    check(len(left) == KEEP and not any(folder.exists() for folder in old[:spent])
          and all(folder.exists() for folder in old[spent:]) and "_CA6_" in left[-1].name,
          f"only the newest {KEEP} failed dungeons are kept, got {[p.name for p in left]}")
    check(f"older failed dungeon folder(s); the newest {KEEP} are kept" in said,
          f"the log says the older ones were cleared, got {said!r}")

    shutil.rmtree(KEPT)
    for shot in RUN.iterdir():
        shot.unlink()
    calibration._FRAME_N = 0
    with contextlib.redirect_stdout(io.StringIO()):
        take("frame", 3000)
    took = []
    for trial in range(3):
        began = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            calibration.keep_failed(f"T{trial}", [(1000, 1019), (2000, 2019)])
        took.append((time.perf_counter() - began) * 1000)
    made = folders()
    check(len(made) == 3 and all(len(numbers(p)) == 40 for p in made),
          f"40 of 3,000 frames are kept each time, got {[len(numbers(p)) for p in made]}")
    print(f"keep_failed on 3,000 frames, 40 kept: median {statistics.median(took):.0f} ms "
          f"({', '.join(f'{ms:.0f}' for ms in took)})")
finally:
    (dungeon.run_sequence, dungeon.right_click, dungeon.exit_dungeon,
     dungeon.focus_game, dungeon.press_buttons, dungeon.internet_now,
     dungeon.END_TIMEOUT, dungeon.STATE, calibration.snap,
     calibration.RUN_FRAMES, calibration.FAILED_DIR, calibration.FRAMES_ON,
     calibration._FRAME_N, active) = saved
    dungeon.ACTIVE.clear()
    dungeon.ACTIVE.update(active)
    dungeon.OVER.clear()
    dungeon.ENDED.clear()
    shutil.rmtree(SCRATCH, ignore_errors=True)

check((dungeon.STATE.read_bytes() if dungeon.STATE.exists() else None) == LIVE,
      "the live dungeon_next.json is left alone")
check((sorted(p.name for p in calibration.FAILED_DIR.iterdir())
       if calibration.FAILED_DIR.is_dir() else None) == LIVE_KEPT,
      "the live failed_dungeons folder is left alone")
check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
