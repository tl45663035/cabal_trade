import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent.parent / "src_1080p"
os.environ["CABAL_CONFIG"] = "cang"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(SRC.parent / "tools"))
import no_input
TRIPPED = no_input.arm()
import dungeon_replay
import record_all

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def fake_dungeon(folder):
    calibration = types.SimpleNamespace(
        _OWN_CTRL=0, STOPPED=threading.Event(), snaps=[],
        snap=lambda name: calibration.snaps.append(name),
        halt_if_stopped=lambda: None)
    return types.SimpleNamespace(
        calibration=calibration, SEQUENCES=folder, KEY_LOCK=threading.Lock(),
        PLAYING=threading.Event(), OVER=threading.Event(),
        ENDED=threading.Event(), WAIT_SLICE=0.01,
        END={"at": None, "defeated": False, "overtime": False})


def ends(d, defeated=False, overtime=False):
    d.END.update(at=time.monotonic(), defeated=defeated, overtime=overtime)
    d.OVER.set()
    d.ENDED.set()


class Player:
    played = 3

    def timing(self):
        return "on time"


scratch = Path(tempfile.mkdtemp())
(scratch / "ic2").mkdir()
(scratch / "ic2" / "run.jsonl").write_text("{}\n", encoding="utf-8")
real_play = dungeon_replay.play

d = fake_dungeon(scratch)
calls = []
dungeon_replay.play = lambda *a, **k: calls.append(1)
try:
    dungeon_replay.run(d, "ic2", "missing.jsonl", verbose=False)
    check(False, "a missing recording is refused")
except RuntimeError as exc:
    check("no recording" in str(exc), "a missing recording is refused")
check(not calls and not d.PLAYING.is_set(),
      "nothing is played for a missing recording")

d = fake_dungeon(scratch)
seen = {}


def playing(path, on_ctrl=None, halt=None, stop=None):
    seen["playing"] = d.PLAYING.is_set()
    seen["halt_before"] = halt()
    ends(d)
    seen["halt_after"] = halt()
    seen["path"] = path
    return Player(), 3, 1.5


dungeon_replay.play = playing
dungeon_replay.run(d, "ic2", "run.jsonl", verbose=False)
check(seen["playing"], "heal and chat are held off while the replay plays")
check(not d.PLAYING.is_set(), "they come back once it is over")
check(seen["path"] == scratch / "ic2" / "run.jsonl",
      "the recording is the dungeon's file in its sequences folder")
check(not seen["halt_before"] and not seen["halt_after"],
      "a cleared dungeon lets the replay play on to its own Confirmation")
check(d.calibration.snaps == ["replayed_ic2"], "a frame is kept after it")

for what, kind in (("the time limit", {"overtime": True}),
                   ("a defeat", {"defeated": True})):
    d = fake_dungeon(scratch)

    def stopped(path, on_ctrl=None, halt=None, kind=kind, stop=None):
        ends(d, **kind)
        seen["halt"] = halt()
        return Player(), 3, 1.5
    dungeon_replay.play = stopped
    dungeon_replay.run(d, "ic2", "run.jsonl", verbose=False)
    check(not seen["halt"], f"{what} leaves the replay to play out")

d = fake_dungeon(scratch)


def ctrl_stop(path, on_ctrl=None, halt=None, stop=None):
    d.calibration.STOPPED.set()
    seen["halt"] = halt()
    d.OVER.set()
    return Player(), 3, 1.5


dungeon_replay.play = ctrl_stop
dungeon_replay.run(d, "ic2", "run.jsonl", verbose=False)
check(seen["halt"], "a Ctrl stop stops the replay")

d = fake_dungeon(scratch)


def ends_early(path, on_ctrl=None, halt=None, stop=None):
    threading.Timer(0.1, d.OVER.set).start()
    return Player(), 3, 1.5


dungeon_replay.play = ends_early
started = time.monotonic()
dungeon_replay.run(d, "ic2", "run.jsonl", verbose=False)
check(time.monotonic() - started >= 0.09,
      "a replay over before the end screen waits for it")

d = fake_dungeon(scratch)


def crashes(path, on_ctrl=None, halt=None, stop=None):
    raise RuntimeError("refused")


dungeon_replay.play = crashes
try:
    dungeon_replay.run(d, "ic2", "run.jsonl", verbose=False)
except RuntimeError:
    pass
check(not d.PLAYING.is_set(), "a failed replay still lets heal and chat back")

d = fake_dungeon(scratch)


def gets_stop(path, on_ctrl=None, halt=None, stop=None):
    seen["stop"] = stop
    d.OVER.set()
    return Player(), 3, 1.5


dungeon_replay.play = gets_stop
dungeon_replay.run(d, "ic2", "run.jsonl", verbose=False)
check(seen["stop"] is d.calibration.STOPPED,
      "the replay thread watches the Ctrl stop itself, not through the main thread")
dungeon_replay.play = real_play

calibration = types.SimpleNamespace(_OWN_CTRL=0)
held = dungeon_replay.holds_ctrl(calibration)
held(True)
check(calibration._OWN_CTRL == 1, "the replay's Ctrl is not the stop key")
held(False)
check(calibration._OWN_CTRL == 1, "for a moment after it lets go too")
time.sleep(record_all.OWN_GRACE + 0.1)
check(calibration._OWN_CTRL == 0, "then the stop key is back")

source = (SRC / "dungeon.py").read_text(encoding="utf-8")
check('elif name == "replay" and dungeon_name in ("ic2", "tm"):' in source,
      "dungeon.py takes the replay step for ic2 and tm only")

check(not TRIPPED, f"tests sent real input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
