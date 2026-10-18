import contextlib
import io
import json
import os
import sys
import tempfile
import time
import types
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
import record_action

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_walk_frames_"))
width, height = record_action.screen()
events = [{"kind": "start", "at": "2026-10-17T23:10:00", "screen": [width, height],
           "cursor": [815, 754]}]
events += [{"t": round(0.1 + 0.25 * i, 3), "kind": "mouse", "action": "move",
            "x": 815 + i, "y": 754 - i} for i in range(11)]
events += [{"t": 0.5, "kind": "key", "action": "down", "vk": 49, "scan": 2,
            "extended": False, "name": "1", "repeat": False},
           {"t": 0.6, "kind": "key", "action": "up", "vk": 49, "scan": 2,
            "extended": False, "name": "1", "repeat": False}]
events = [events[0]] + sorted(events[1:], key=lambda e: e["t"])
(SCRATCH / "chaos_arena").mkdir()
for name in ("get_to_gate", "get_to_middle"):
    (SCRATCH / "chaos_arena" / f"{name}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8")

shots, sent = [], []
saved = (dungeon.SEQUENCES, dungeon.focus_game, dungeon.ctypes, calibration.snap,
         record_action.send)
dungeon.SEQUENCES = SCRATCH
dungeon.focus_game = lambda *a, **k: True
dungeon.ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(
    user32=types.SimpleNamespace(SetCursorPos=lambda x, y: None)))
calibration.snap = lambda label, *a, **k: shots.append((label, time.monotonic()))
record_action.send = lambda *a, **k: sent.append(a)
try:
    check(dungeon.WALK_FRAMES == {"get_to_gate": 1.0},
          f"config walk_frames gives a frame a second in the first walk only, got {dungeon.WALK_FRAMES}")
    said = io.StringIO()
    started = time.monotonic()
    with contextlib.redirect_stdout(said):
        dungeon.play("chaos_arena", "get_to_gate")
    took = time.monotonic() - started
    walk = [(label, at - started) for label, at in shots if label.startswith("get_to_gate_")]
    labels = [label for label, _at in walk]
    check(labels == ["get_to_gate_0s", "get_to_gate_1s", "get_to_gate_2s"],
          f"a 2.6 s walk gets frames at 0, 1 and 2 s, got {labels} over {took:.2f}s")
    gaps = [b - a for (_l, a), (_m, b) in zip(walk, walk[1:])]
    check(all(0.85 <= gap <= 1.2 for gap in gaps), f"the frames are about 1 s apart, got {gaps}")
    check(shots and shots[-1][0] == "played_get_to_gate",
          f"the frame after the walk is still taken last, got {[s[0] for s in shots]}")
    check("a frame every 1s while it plays" in said.getvalue(),
          f"the log says the walk is filmed: {said.getvalue()!r}")
    check(len(sent) == 2, f"both key events were sent through the stub, got {len(sent)}")
    count = len(shots)
    time.sleep(1.5)
    check(len(shots) == count, "no frame is taken after the walk ends")

    shots.clear()
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.play("chaos_arena", "get_to_middle")
    check([label for label, _at in shots] == ["played_get_to_middle"],
          f"the second walk keeps only its frame after the walk, got {shots}")
    check("a frame every" not in said.getvalue(), "the second walk's log line is unchanged")
finally:
    (dungeon.SEQUENCES, dungeon.focus_game, dungeon.ctypes, calibration.snap,
     record_action.send) = saved

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
