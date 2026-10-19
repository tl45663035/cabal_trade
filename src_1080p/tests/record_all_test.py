import json
import os
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
import no_input
TRIPPED = no_input.arm()
import record_action as ra
import record_all

for name in ("SendInput", "SetCursorPos"):
    setattr(ra.user32, name, no_input._raiser(name))

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


W = ra.W
SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_record_all_"))
ra.OUT = SCRATCH
CTRL = W["VK_CONTROL"]
INJ_KEY = W["LLKHF_INJECTED"]
INJ_MOUSE = W["LLMHF_INJECTED"]


def key(session, vk, down, injected=False, scan=0):
    info = ra.KBDLLHOOKSTRUCT(vkCode=vk, scanCode=scan,
                              flags=INJ_KEY if injected else 0)
    session.key(W["WM_KEYDOWN"] if down else W["WM_KEYUP"], info)


def tap(session, vk, injected=False, scan=0):
    key(session, vk, True, injected, scan)
    key(session, vk, False, injected, scan)


def mouse(session, message, x, y, injected=False):
    info = ra.MSLLHOOKSTRUCT(pt=wintypes.POINT(x, y), mouseData=0,
                             flags=INJ_MOUSE if injected else 0)
    session.mouse(message, info)


def lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


# The bot's own Ctrl never stops the recording; two physical taps do, and
# those two taps are left out of the saved file.
session = record_all.Everything()
tap(session, CTRL, injected=True)
tap(session, CTRL, injected=True)
check(not session.finished, "two injected Ctrl taps must not stop recording")
check(len(session.events) == 4, f"injected keys are recorded, got {len(session.events)}")
mouse(session, W["WM_LBUTTONDOWN"], 100, 200, injected=True)
tap(session, CTRL)
tap(session, CTRL)
check(session.finished, "two physical Ctrl taps stop recording")
session.begin_file()
session.finish({})
saved = lines(session.path)
check(saved[0]["kind"] == "start" and saved[0]["recorder"] == "record_all",
      f"header first, got {saved[0]}")
kept = saved[1:]
check(len(kept) == 5, f"the stop gesture is dropped and the rest kept, got {len(kept)}")
check(all(e["injected"] for e in kept), "only the injected events remain")
check(kept[-1]["kind"] == "mouse" and kept[-1]["button"] == "left",
      "the bot's click is recorded with its position")

# In run mode the Ctrl gesture is off; the child decides when it ends.
session = record_all.Everything(control=False, command=["x"])
tap(session, CTRL)
tap(session, CTRL)
check(not session.finished, "run mode ignores the Ctrl gesture")

# Typed text never reaches disk, and is labelled after the run.
session = record_all.Everything()
session.begin_file()
VK_END = 35
for ch in "bob":
    tap(session, record_all.VK_PACKET, injected=True, scan=ord(ch))
tap(session, VK_END, injected=True)
for ch in "pw1":
    tap(session, record_all.VK_PACKET, injected=True, scan=ord(ch))
tap(session, VK_END, injected=True)
for ch in "zz":
    tap(session, record_all.VK_PACKET, injected=True, scan=ord(ch))
session.flush()
streamed = session.path.read_text(encoding="utf-8")
check("pw1" not in streamed and '"p"' not in streamed and "bob" not in streamed,
      "the stream holds no typed characters")
session.finish({"username": "bob", "password": "pw1"})
finished = session.path.read_text(encoding="utf-8")
check('"p"' not in finished and '"w"' not in finished and "pw1" not in finished,
      "the final file holds no typed characters")
typed = [e for e in lines(session.path)[1:] if "secret" in e]
check([(e["secret"], e["i"], e["action"]) for e in typed[:6]]
      == [("username", 0, "down"), ("username", 0, "up"), ("username", 1, "down"),
          ("username", 1, "up"), ("username", 2, "down"), ("username", 2, "up")],
      f"the username run is labelled by index, got {typed[:6]}")
check({e["secret"] for e in typed[6:12]} == {"password"}, "the password run is labelled")
check(all(e["secret"] is None for e in typed[12:]), "unmatched text stays unlabelled")
check(not session.path.with_name(session.path.name + ".part").exists(),
      "the rewrite leaves no spare file")

# Cursor moves the hook missed (SetCursorPos) are picked up by polling, once.
session = record_all.Everything()
session.last = (0, 0)
mouse(session, W["WM_MOUSEMOVE"], 10, 10)
check(session.poll((10, 10)) is False, "a move the hook saw is not recorded twice")
check(session.poll((50, 60)) is True, "a move the hook missed is recorded")
check(session.poll((50, 60)) is False, "an unchanged cursor records nothing")
polled = [e for e in session.events if e.get("source") == "poll"]
check(len(polled) == 1 and (polled[0]["x"], polled[0]["y"]) == (50, 60),
      f"one polled move at the new spot, got {polled}")

# Replay sends everything in order, fills typed text from the account, and
# lets go of whatever the recording left held.
sent = []
real_send = ra.send
ra.send = lambda item: sent.append(item)
try:
    events = [
        {"t": 0.0, "kind": "key", "action": "down", "vk": 65, "scan": 30,
         "extended": False, "name": "A", "repeat": False, "injected": True},
        {"t": 0.001, "kind": "key", "action": "up", "vk": 65, "scan": 30,
         "extended": False, "name": "A", "repeat": False, "injected": True},
        {"t": 0.002, "kind": "key", "action": "down", "vk": 231,
         "secret": "password", "i": 1, "injected": True},
        {"t": 0.003, "kind": "key", "action": "up", "vk": 231,
         "secret": "password", "i": 1, "injected": True},
        {"t": 0.004, "kind": "key", "action": "down", "vk": 231,
         "secret": None, "injected": True},
        {"t": 0.005, "kind": "mouse", "action": "move", "x": 7, "y": 8,
         "source": "poll"},
        {"t": 0.006, "kind": "mouse", "action": "down", "button": "left",
         "x": 7, "y": 8, "injected": True},
    ]
    player = record_all.Replay(events, [5, 5], {"password": "xy"})
    started = time.perf_counter()
    player.play()
    check(player.finished and player.played == len(events),
          f"every event is played, got {player.played}")
    kinds = [item.type for item in sent]
    check(kinds[0] == W["INPUT_MOUSE"], "the cursor goes to the start position first")
    unicode = [item.u.ki for item in sent if item.type == W["INPUT_KEYBOARD"]
               and item.u.ki.dwFlags & record_all.UNICODE]
    check([(k.wScan, bool(k.dwFlags & W["KEYEVENTF_KEYUP"])) for k in unicode]
          == [(ord("y"), False), (ord("y"), True)],
          "the password's second character is typed from the account")
    last = sent[-1].u.mi
    check(sent[-1].type == W["INPUT_MOUSE"]
          and last.dwFlags & W["MOUSEEVENTF_LEFTUP"],
          "the held left button is released at the end")
finally:
    ra.send = real_send

# Replay refuses a recording that needs a credential it can't find.
width, height = ra.screen()
head = {"kind": "start", "screen": [width, height], "desktop": ra.desktop(),
        "cursor": [1, 1]}
needs = SCRATCH / "needs_password_all.jsonl"
needs.write_text(json.dumps(head) + "\n" + json.dumps(events[2]) + "\n",
                 encoding="utf-8")
saved_account, saved_env = record_all.ACCOUNT, os.environ.pop("CABAL_PASSWORD", None)
record_all.ACCOUNT = SCRATCH / "no_account.json"
try:
    _head, _events, refused = record_all.prepare(needs)
    check(refused and "password" in refused, f"missing password refuses, got {refused}")
finally:
    record_all.ACCOUNT = saved_account
    if saved_env is not None:
        os.environ["CABAL_PASSWORD"] = saved_env
elsewhere = SCRATCH / "elsewhere_all.jsonl"
elsewhere.write_text(json.dumps({**head, "screen": [1, 1]}) + "\n"
                     + json.dumps(events[0]) + "\n", encoding="utf-8")
check("would not line up" in (record_all.prepare(elsewhere)[2] or ""),
      "a different screen refuses")

# Run mode records while the command runs and hands back its exit code.
saved_hook, saved_pump = ra.hook, ra.pump
ra.hook = lambda *a, **k: (None, None)


def pump(done, tick):
    while not done():
        time.sleep(0.01)
        tick()


ra.pump = pump
try:
    code = record_all.record([sys.executable, "-c", "import sys; sys.exit(3)"])
finally:
    ra.hook, ra.pump = saved_hook, saved_pump
check(code == 3, f"run mode returns the command's exit code, got {code}")
made = sorted(SCRATCH.glob(f"*{record_all.SUFFIX}"))
heads = [lines(p)[0] for p in made]
check(any(h.get("command") and h["command"][0] == sys.executable for h in heads),
      "the recording notes the command it ran")

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
