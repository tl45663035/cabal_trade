import contextlib
import io
import os
import sys
import types
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


MINUTE = 60.0
BURST = {"run_minutes": [20, 30], "rest_minutes": [50, 70], "check_every": 30}


def play(burst, turns, route_fails=False):
    clock, events, draws = [0.0], [], []

    def advance(seconds, verbose=True):
        clock[0] += seconds

    def run_dungeon(name, location, verbose=True):
        events.append(("run", round(clock[0] / MINUTE)))
        clock[0] += 5 * MINUTE

    def route(verbose=True):
        events.append(("route", round(clock[0] / MINUTE)))
        if route_fails:
            raise RuntimeError("no 'Exit' in the dungeon window")

    def uniform(low, high):
        draws.append((low, high))
        return (low + high) / 2

    boss_checks = []
    fake_time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None)
    with patched((dungeon, "BURST", burst),
                 (dungeon, "BURST_STATE", {"until": None, "on_channel": False}),
                 (dungeon, "time", fake_time),
                 (dungeon, "random", types.SimpleNamespace(uniform=uniform)),
                 (dungeon, "wait_timer", advance),
                 (dungeon, "run_dungeon", run_dungeon),
                 (dungeon, "get_to_wb_area", route),
                 (dungeon.calibration, "close_everything", lambda *a, **k: None),
                 (dungeon, "world_boss_turn",
                  lambda name, verbose=True: boss_checks.append((name, clock[0])) and False),
                 (dungeon, "start_block", lambda *a, **k: events.append(("block", round(clock[0] / MINUTE)))),
                 (dungeon, "cool_down", lambda *a, **k: None)), \
            contextlib.redirect_stdout(io.StringIO()) as said:
        after = None
        for _ in range(turns):
            after = dungeon.run_in_turn("eop", "EOP", after, True)
    return events, boss_checks, draws, said.getvalue()


events, boss_checks, draws, said = play(BURST, 12)
runs = [minute for kind, minute in events if kind == "run"]
routes = [minute for kind, minute in events if kind == "route"]
check("a 25-minute burst runs until its time is up, then goes to the world boss stage",
      runs[:5] == [0, 5, 10, 15, 20] and routes[:1] == [25], f"{runs} {routes}")
check("the rest lasts the drawn 60 minutes, then the next burst starts",
      runs[5:10] == [85, 90, 95, 100, 105] and routes[1:2] == [110], f"{runs} {routes}")
check("each burst and each rest is drawn from its own range",
      draws[:3] == [(20, 30), (50, 70), (20, 30)], str(draws))
check("the world boss is checked every 30s through the rest",
      len([c for c in boss_checks if c[0] is None and 25 * MINUTE <= c[1] < 85 * MINUTE]) == 120,
      str(len(boss_checks)))
check("a burst after a rest starts like a fresh block, with its buffs",
      ("block", 85) in events, str(events))
check("the log says when the rest ends", "resting 60.0 min at the world boss stage" in said, said[-600:])

events, boss_checks, draws, said = play(BURST, 8, route_fails=True)
runs = [minute for kind, minute in events if kind == "run"]
check("a route that fails still rests and carries on",
      runs[5:] == [85, 90, 95] and "world boss stage not reached" in said, f"{runs} {said[-300:]}")

def rest_with_window(seen_open, boss_at_minute=None):
    events, clock = [], [0.0]
    spec = {"key": 67, "window": [100, 110, 260, 138], "word": "Character"}
    burst = dict(BURST, character_window=spec)
    window = {"open": False}

    def press(key):
        events.append(("press", key))
        if key == 67:
            window["open"] = not window["open"]

    def boss(name, verbose=True):
        if (boss_at_minute is not None and clock[0] >= boss_at_minute * MINUTE
                and ("boss", boss_at_minute) not in events and name is None):
            events.append(("boss", boss_at_minute))
            window["open"] = False
            return True
        return False

    def advance(seconds, verbose=True):
        clock[0] += seconds

    fake_time = types.SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda s: None)
    with patched((dungeon, "BURST", burst), (dungeon, "time", fake_time),
                 (dungeon, "random", types.SimpleNamespace(uniform=lambda a, b: (a + b) / 2)),
                 (dungeon, "wait_timer", advance), (dungeon, "press", press),
                 (dungeon, "wait_for", lambda window_box, word, timeout:
                  (seen_open and window["open"], "Character" if window["open"] else "")),
                 (dungeon, "get_to_wb_area", lambda verbose=True: events.append(("route", 0))),
                 (dungeon.calibration, "close_everything", lambda *a, **k: None),
                 (dungeon, "world_boss_turn", boss)), \
            contextlib.redirect_stdout(io.StringIO()):
        dungeon.rest_between_bursts(True)
    return events, window["open"]


events, still_open = rest_with_window(True)
check("the rest opens the Character window with C after reaching the stage, and closes it at the end",
      events == [("route", 0), ("press", 67), ("press", 67)] and not still_open, str(events))
events, still_open = rest_with_window(True, boss_at_minute=10)
check("a world boss during the rest is followed by C again, and the window is closed at the end",
      events == [("route", 0), ("press", 67), ("boss", 10), ("press", 67), ("press", 67)]
      and not still_open, str(events))
events, still_open = rest_with_window(False)
check("a window that can't be read as open is not toggled again at the end",
      events == [("route", 0), ("press", 67)], str(events))

events, boss_checks, draws, said = play(None, 8)
check("with no burst setting the runs go back to back with no rest",
      [m for k, m in events if k == "run"] == [0, 5, 10, 15, 20, 25, 30, 35]
      and not [e for e in events if e[0] == "route"], str(events))

relogs = []
with patched((dungeon, "NO_BREAKS", True),
             (dungeon, "relog_for_burst", lambda verbose=True: relogs.append(True))):
    events, boss_checks, draws, said = play(dict(BURST, regions=[{"name": "X"}]), 12)
runs = [minute for kind, minute in events if kind == "run"]
check("with --no-breaks the runs go back to back, never resting at the world boss stage",
      runs == [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55]
      and not [e for e in events if e[0] == "route"], str(events))
check("with --no-breaks each new burst still relogs into a night channel",
      len(relogs) == 3 and ("block", 25) in events, f"{len(relogs)} {events}")

import recovery
relogged = []
with patched((dungeon, "NO_BREAKS", True), (dungeon, "CHANNEL", 11),
             (recovery, "relog", lambda verbose=True, channel=None: relogged.append(channel))):
    events, boss_checks, draws, said = play(dict(BURST, regions=[{"name": "X", "channels": [21]}]), 12)
check("with --channel 11 it relogs into Channel 11 once and stays there for every later burst",
      relogged == [11] and said.count("staying on Channel 11") == 2, f"{relogged} {said.count('staying on Channel 11')}")
check("and still never rests", not [e for e in events if e[0] == "route"], str(events))

check("Cang never picks Channel 1 for a night relog; it is kept for the world boss",
      all(1 not in region["channels"] for region in dungeon.BURST["regions"]),
      str(dungeon.BURST["regions"]))

check("Cang runs in bursts of 20-30 minutes with 20-30 minute rests",
      dungeon.BURST is not None and dungeon.BURST["run_minutes"] == [20, 30]
      and dungeon.BURST["rest_minutes"] == [20, 30], str(dungeon.BURST))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
