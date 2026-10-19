import contextlib
import io
import json
import os
import sys
import tempfile
import threading
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
import record_action as ra
import record_all

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_dungeon_replay_"))
saved = (ra.OUT, ra.send, record_all.game_rect, dict(dungeon._DUNGEON))
ra.OUT = SCRATCH
sent = []
ra.send = lambda item: sent.append(item)
record_all.game_rect = lambda: None
width, height = ra.screen()
head = {"kind": "start", "at": "2026-10-19T16:59:54", "screen": [width, height],
        "desktop": ra.desktop(), "cursor": [5, 5], "window": None, "start_shot": None}
events = [{"t": 0.0, "kind": "key", "action": "down", "vk": 87, "scan": 17,
           "extended": False, "name": "W", "repeat": False},
          {"t": 0.002, "kind": "key", "action": "up", "vk": 87, "scan": 17,
           "extended": False, "name": "W", "repeat": False}]
(SCRATCH / "loop_all.jsonl").write_text(
    "\n".join(json.dumps(x) for x in [head] + events) + "\n", encoding="utf-8")


def run(**kw):
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        done = dungeon.replay_recording(**kw)
    return done, said.getvalue()


try:
    dungeon._DUNGEON["replay"] = {"file": "loop_all.jsonl", "times": 3, "break_seconds": 0.2}
    started = time.monotonic()
    done, said = run()
    took = time.monotonic() - started
    check(done == 3, f"the config's 3 replays all play, got {done}")
    check(said.count("resting 0.2s") == 2, "it rests between replays, not after the last")
    check(took >= 0.4, f"the rests are actually waited, took {took:.2f}s")
    keys = [i for i in sent if i.type == ra.W["INPUT_KEYBOARD"]]
    check(len(keys) == 6, f"each replay sends the recording once, got {len(keys)} keys")
    check("replay 3 of 3" in said and "3 of 3 replay(s) played to the end" in said,
          "the progress and the total are printed")

    sent.clear()
    done, said = run(name=str(SCRATCH / "loop_all.jsonl"), times=1, rest=0)
    check(done == 1, "a full path and explicit counts override the config")

    sent.clear()
    dungeon._DUNGEON["replay"] = {"file": "loop_all.jsonl", "times": 30, "break_seconds": 5}
    calibration.STOPPED.clear()
    threading.Timer(0.3, calibration.STOPPED.set).start()
    started = time.monotonic()
    done, said = run()
    took = time.monotonic() - started
    check(done == 1 and took < 3, f"the Ctrl stop ends the loop during a rest, "
                                  f"got {done} in {took:.2f}s")
    check("stopped by the Ctrl stop" in said, "the stop is reported")
    calibration.STOPPED.clear()

    for setting, words in (({"file": None}, "set dungeon.replay.file"),
                           ({"file": "missing_all.jsonl"}, "no recording")):
        dungeon._DUNGEON["replay"] = setting
        try:
            run()
            check(False, f"{setting} should refuse")
        except SystemExit as exc:
            check(words in str(exc), f"{setting} refuses with {words!r}, got {exc}")

    (SCRATCH / "wrong_all.jsonl").write_text(
        json.dumps({**head, "screen": [1, 1]}) + "\n" + json.dumps(events[0]) + "\n",
        encoding="utf-8")
    dungeon._DUNGEON["replay"] = {"file": "wrong_all.jsonl", "times": 2}
    sent.clear()
    try:
        run()
        check(False, "a recording from another screen should refuse")
    except SystemExit as exc:
        check("would not line up" in str(exc) and not sent,
              f"another screen refuses before sending anything, got {exc}")

    ctrl = [{**events[0], "vk": 17, "t": 0.0}, {**events[1], "vk": 17, "t": 0.001}]
    (SCRATCH / "ctrl_all.jsonl").write_text(
        "\n".join(json.dumps(x) for x in [head] + ctrl) + "\n", encoding="utf-8")
    dungeon._DUNGEON["replay"] = {"file": "ctrl_all.jsonl", "times": 1}
    done, said = run()
    check("presses Ctrl 1 time(s)" in said, "a recording that presses Ctrl is flagged")

    loaded = calibration.load_shared()["dungeon"]
    check(loaded["replay"]["times"] == 1, "the default config replays once")
    cang = json.loads((SRC / "configs" / "cang.json").read_text(encoding="utf-8"))
    check(cang["dungeon"]["replay"] == {"file": "2026-10-19_165954_all.jsonl",
                                        "times": 30, "break_seconds": 30},
          "Cang replays its recording 30 times with 30s between")
finally:
    ra.OUT, ra.send, record_all.game_rect = saved[:3]
    dungeon._DUNGEON.clear()
    dungeon._DUNGEON.update(saved[3])

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
