"""Recorded dungeon runs (src_1080p/recorded_dungeon.py): settings and file
lookup, one run (entry, Challenge, the replay, the end), and the rotation
inside the trading run. The game is pretend: fake_input.py takes the
replay's input, and the bot's own steps and screen reads are scripted."""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["dungeon.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import dungeon
import record_action as ra
import record_all
import recorded_dungeon as rd
from fake_input import FakeInput, Script, patched, record_session

for name in ("SendInput", "SetCursorPos", "SetForegroundWindow", "BringWindowToTop",
             "AttachThreadInput", "ShowWindow"):
    setattr(ra.user32, name, no_input._raiser(name))

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_recorded_dungeon_"))
SEQ = SCRATCH / "dungeon"
OUT = SCRATCH / "recordings"
for folder in (SEQ / "fire", SEQ / "ice", OUT):
    folder.mkdir(parents=True)
LAYOUT = (0, 0, 1920, 1080)


def make_recording(path, script, cursor=(960, 540)):
    """A real record_all recording of script, saved as path."""
    with contextlib.redirect_stdout(io.StringIO()), \
            patched((record_all, "game_rect", lambda: None)):
        made, fake = record_session(script, SCRATCH, fake=FakeInput(layout=LAYOUT,
                                                                     cursor=cursor))
    path.write_bytes(made.read_bytes())
    made.unlink()
    return path


def walk(seconds=2.0, step=200):
    s = Script().ctrl_twice("start")
    for _ in range(int(seconds * 1000 / step)):
        s.tap("W", hold=40, gap=step - 40)
    return s.ctrl_twice("stop")


DUNGEONS = {
    "fire": {"locations": {"Fire": {"entry": [185, 401], "name": "Lava Hellfire"}},
             "daily_limit": 30, "start_at": "01:40", "cooldown": 35, "end_wait": 2,
             "before_exit": ["cancel_bm"],
             "exit": [["button", "confirmation", 0.0], ["button", "fire_leave", 0.0]],
             "after_exit": []},
    "ice": {"locations": {"Ice": {"entry": [10, 10], "name": "Ice"}},
            "daily_limit": 5, "cooldown": 0},
    "arena": {"locations": {"A1": {"entry": [1, 1], "name": "Chaos Arena Lv.1"},
                            "A2": {"entry": [2, 2], "name": "Chaos Arena Lv.2"}}},
}
make_recording(SEQ / "fire" / "run.jsonl", walk())
make_recording(SEQ / "fire" / "out.jsonl", walk(0.6))
make_recording(OUT / "ice_run.jsonl", walk())
make_recording(OUT / "ice_out.jsonl", walk(0.6))
make_recording(SEQ / "fire" / "both.jsonl", walk(0.4))
make_recording(OUT / "both.jsonl", walk(0.4))
elsewhere = SEQ / "fire" / "elsewhere.jsonl"
lines = (SEQ / "fire" / "run.jsonl").read_text(encoding="utf-8").splitlines()
head = json.loads(lines[0])
head["desktop"] = [0, 0, 5360, 1440]
elsewhere.write_text("\n".join([json.dumps(head)] + lines[1:]) + "\n", encoding="utf-8")

RECORDED = {
    "fire": {"enabled": True, "recording": "run.jsonl", "exit_recording": "out.jsonl",
             "timeout_seconds": 30, "cooldown": 10},
    "arena": {"enabled": False, "recording": "run.jsonl", "exit_recording": "out.jsonl",
              "timeout_seconds": 30},
    "ice": {"enabled": True, "recording": "ice_run.jsonl",
            "exit_recording": "ice_out.jsonl", "timeout_seconds": 30},
}


@contextlib.contextmanager
def world(recorded=None, dungeons=None):
    """The pretend config, folders and screen for one test."""
    with patched((dungeon, "DUNGEONS", dict(dungeons or DUNGEONS)),
                 (dungeon, "_DUNGEON", {**dungeon._DUNGEON,
                                        "recorded": dict(recorded or RECORDED)}),
                 (rd, "SEQUENCES", SEQ), (ra, "OUT", OUT),
                 (ra, "screen", lambda: [1920, 1080]),
                 (ra, "desktop", lambda: list(LAYOUT)),
                 (record_all, "game_rect", lambda: None)):
        yield


# ============================== Task 1 ==============================

with world():
    check(rd.enabled() == ["fire", "ice"],
          f"enabled_in_config_order: got {rd.enabled()}")
    fire = rd.settings("fire")
    check(fire["cooldown"] == 10 and fire["exit"] == DUNGEONS["fire"]["exit"]
          and fire["recording"] == "run.jsonl" and fire["start_at"] == "01:40",
          f"settings_overlay: a recorded cooldown beats the dungeon's, the rest "
          f"comes through, got {fire}")
    check(rd.find("fire", "both.jsonl") == SEQ / "fire" / "both.jsonl",
          "find_order: the dungeon folder comes first")
    check(rd.find("ice", "ice_run.jsonl") == OUT / "ice_run.jsonl",
          "find_order: then the recordings folder")
    full = SEQ / "fire" / "run.jsonl"
    check(rd.find("ice", str(full)) == full, "find_order: a full path is itself")
    check(rd.find("fire", "nope.jsonl") is None, "find_order: missing is None")
    check(rd.problem("fire") is None, f"problem: a good entry has none, got "
                                      f"{rd.problem('fire')}")

cases = [
    ("arena", {"arena": {**RECORDED["arena"], "enabled": True}}, "one location"),
    ("fire", {"fire": {**RECORDED["fire"], "recording": "nope.jsonl"}}, "recording"),
    ("fire", {"fire": {**RECORDED["fire"], "exit_recording": "nope.jsonl"}}, "exit"),
    ("fire", {"fire": {k: v for k, v in RECORDED["fire"].items()
                       if k != "timeout_seconds"}}, "timeout"),
    ("fire", {"fire": {**RECORDED["fire"], "recording": "elsewhere.jsonl"}},
     "would not line up"),
]
for name, recorded, word in cases:
    with world(recorded):
        said = rd.problem(name) or ""
    check(word in said, f"problem_reasons: {word!r} expected, got {said!r}")

# ============================== the end ==============================

check(not TRIPPED, f"tests sent real input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
