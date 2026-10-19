import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import types
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


W = ra.W
SCRATCH = Path(tempfile.mkdtemp(prefix="cabal_record_all_"))
ra.OUT = SCRATCH
CTRL = W["VK_CONTROL"]
VK_A, VK_W, VK_END = 65, 87, 35
INJ_KEY = W["LLKHF_INJECTED"]
INJ_MOUSE = W["LLMHF_INJECTED"]
WIDTH, HEIGHT = ra.screen()
DESKTOP = ra.desktop()
record_all.game_rect = lambda: None


class Clock:
    def __init__(self, at=1000.0):
        self.at = at

    def __call__(self):
        return self.at

    def step(self, by):
        self.at += by


@contextlib.contextmanager
def fake_clock():
    clock = Clock()
    real = record_all.time
    record_all.time = types.SimpleNamespace(perf_counter=clock, sleep=lambda s: None)
    try:
        yield clock
    finally:
        record_all.time = real


def key(session, vk, down, injected=False, scan=0, extended=False, system=False):
    flags = (INJ_KEY if injected else 0) | (W["LLKHF_EXTENDED"] if extended else 0)
    info = ra.KBDLLHOOKSTRUCT(vkCode=vk, scanCode=scan, flags=flags)
    if system:
        message = W["WM_SYSKEYDOWN"] if down else W["WM_SYSKEYUP"]
    else:
        message = W["WM_KEYDOWN"] if down else W["WM_KEYUP"]
    session.key(message, info)


def tap(session, vk, injected=False, scan=0):
    key(session, vk, True, injected, scan)
    key(session, vk, False, injected, scan)


def mouse(session, message, x, y, injected=False, data=0):
    info = ra.MSLLHOOKSTRUCT(pt=wintypes.POINT(x, y), mouseData=data,
                             flags=INJ_MOUSE if injected else 0)
    session.mouse(message, info)


def lines(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def write(path, head, events, tail=""):
    path.write_text("\n".join(json.dumps(x) for x in [head] + events) + "\n" + tail,
                    encoding="utf-8")
    return path


HEAD = {"kind": "start", "at": "2026-10-19T15:40:52", "screen": [WIDTH, HEIGHT],
        "desktop": DESKTOP, "cursor": [5, 5], "command": None, "window": None,
        "start_shot": None, "recorder": "record_all"}


# ---- recording by hand: armed, Ctrl x2 starts, Ctrl x2 stops ----

with fake_clock() as clock:
    session = record_all.Everything(armed=True)
    check(not session.recording and session.events == [], "armed starts idle")
    tap(session, VK_W)
    mouse(session, W["WM_LBUTTONDOWN"], 10, 10)
    check(session.events == [], "nothing is kept before the start gesture")
    check(session.poll((40, 40)) is False, "polling is idle before the start")
    tap(session, CTRL, injected=True)
    clock.step(0.1)
    tap(session, CTRL, injected=True)
    check(not session.recording, "the bot's Ctrl x2 can't start a recording")
    tap(session, CTRL)
    clock.step(2.0)
    tap(session, CTRL)
    check(not session.recording, "Ctrl taps further apart than the window don't start")
    clock.step(2.0)
    tap(session, CTRL)
    clock.step(0.1)
    mouse(session, W["WM_LBUTTONDOWN"], 10, 10)
    tap(session, CTRL)
    check(not session.recording, "a click between the taps cancels the gesture")
    clock.step(1.0)
    tap(session, CTRL)
    clock.step(0.1)
    tap(session, CTRL)
    check(session.recording and session.events == [],
          "Ctrl x2 starts recording and the gesture itself is not kept")
    started_at = clock.at
    clock.step(0.5)
    tap(session, VK_A, scan=30)
    check(session.events[0]["t"] == 0.5, f"time counts from the start, got {session.events[0]}")
    clock.step(0.5)
    tap(session, CTRL)
    clock.step(0.1)
    tap(session, CTRL)
    check(session.finished, "Ctrl x2 again stops it")
    session.open()
    session.finish({})
    saved = lines(session.path)
    check([e.get("name") for e in saved[1:]] == ["A", "A"],
          f"the stop gesture is dropped and the rest kept, got {saved[1:]}")
    check(saved[0]["kind"] == "start" and saved[0]["recorder"] == "record_all",
          f"header first, got {saved[0]}")

# ---- what gets recorded ----

with fake_clock() as clock:
    session = record_all.Everything()
    check(session.recording, "not armed records straight away")
    key(session, VK_W, True, scan=17)
    clock.step(0.03)
    key(session, VK_W, True, scan=17)
    key(session, VK_W, True, scan=17)
    key(session, VK_W, False, scan=17)
    check([e["repeat"] for e in session.events] == [False, True, True, False],
          "held keys are marked as auto-repeat")
    key(session, 18, True, system=True, scan=56)
    check(session.events[-1]["action"] == "down", "Alt (a system key) counts as a key down")
    key(session, 18, False, system=True, scan=56)
    key(session, 0x25, True, scan=75, extended=True)
    check(session.events[-1]["extended"] is True, "the extended flag is kept")
    key(session, 0x25, False, scan=75, extended=True)
    tap(session, VK_A, injected=True, scan=30)
    check(session.events[-1]["injected"] is True and session.events[-3]["injected"] is False,
          "bot keys and hand keys are told apart")
    wheel_down = (-120 & 0xFFFF) << 16
    mouse(session, W["WM_MOUSEWHEEL"], 300, 400, data=wheel_down)
    check(session.events[-1]["action"] == "wheel" and session.events[-1]["delta"] == -120,
          f"a wheel turn keeps its signed delta, got {session.events[-1]}")
    mouse(session, W["WM_MOUSEHWHEEL"], 300, 400, data=120 << 16)
    check(session.events[-1]["action"] == "hwheel", "horizontal wheel is recorded")
    mouse(session, W["WM_XBUTTONDOWN"], 300, 400, data=W["XBUTTON2"] << 16)
    check(session.events[-1]["button"] == "x2", "the side button is identified")
    mouse(session, W["WM_RBUTTONDOWN"], 300, 400, injected=True)
    check(session.events[-1]["button"] == "right" and session.events[-1]["injected"],
          "the bot's right click is recorded")
    count = len(session.events)
    mouse(session, 0x020E + 99, 1, 1)
    check(len(session.events) == count, "unknown mouse messages are ignored")

# Run mode: the Ctrl gesture is off; the command decides when it ends.
session = record_all.Everything(control=False, command=["x"])
tap(session, CTRL)
tap(session, CTRL)
check(not session.finished, "run mode ignores the Ctrl gesture")

# ---- typed text never reaches disk ----

session = record_all.Everything()
session.open()
for ch in "bob":
    tap(session, record_all.VK_PACKET, injected=True, scan=ord(ch))
tap(session, VK_END, injected=True)
for ch in "pw1":
    tap(session, record_all.VK_PACKET, injected=True, scan=ord(ch))
    mouse(session, W["WM_MOUSEMOVE"], 5, 5, injected=True)
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
check([e["secret"] for e in typed[6:12]] == ["password"] * 6,
      "a run with mouse moves in between is still one run")
check(all(e["secret"] is None for e in typed[12:]), "unmatched text stays unlabelled")
check(not session.path.with_name(session.path.name + ".part").exists(),
      "the rewrite leaves no spare file")

events = [{"t": 0, "kind": "key", "action": "up", "vk": 231, "secret": None},
          {"t": 1, "kind": "key", "action": "down", "vk": 231, "secret": None},
          {"t": 2, "kind": "key", "action": "up", "vk": 231, "secret": None}]
record_all.label_typed(events, {id(events[0]): "x", id(events[1]): "q",
                                id(events[2]): "q"}, {"password": "q"})
check([e.get("i") for e in events] == [0, 0, 0] and events[0]["secret"] == "password",
      "an up with no down before it still gets a valid index")
events = [{"t": 0, "kind": "key", "action": "down", "vk": 231, "secret": None}]
record_all.label_typed(events, {id(events[0]): "s"}, {"username": "s", "password": "s"})
check(events[0]["secret"] == "username", "same username and password: the first wins")
events = [{"t": 0, "kind": "key", "action": "down", "vk": 231, "secret": None}]
record_all.label_typed(events, {id(events[0]): "s"}, {"username": None, "password": ""})
check(events[0]["secret"] is None, "blank credentials never match")

# ---- cursor moves the hook missed are picked up by polling, once ----

with fake_clock() as clock:
    session = record_all.Everything()
    session.last = (0, 0)
    mouse(session, W["WM_MOUSEMOVE"], 10, 10)
    check(session.poll((10, 10)) is False, "a move the hook saw is not recorded twice")
    check(session.poll((50, 60)) is True, "a move the hook missed is recorded")
    check(session.poll((50, 60)) is False, "an unchanged cursor records nothing")
    mouse(session, W["WM_MOUSEMOVE"], 70, 70)
    clock.step(record_all.EXPLAINED + 0.05)
    check(session.poll((70, 70)) is True,
          "a hook move too old to explain the cursor is recorded again")
    polled = [e for e in session.events if e.get("source") == "poll"]
    check([(e["x"], e["y"]) for e in polled] == [(50, 60), (70, 70)],
          f"polled moves land where the cursor went, got {polled}")
    check(all("injected" not in e for e in polled), "polled moves don't claim a source")

# ---- saving as it goes, and the final file ----

session = record_all.Everything()
session.path = SCRATCH / "unopened_all.jsonl"
session.flush()
check(not session.path.exists(), "nothing is written before the file is opened")
taken = record_all.Everything()
taken.open()
twin = record_all.Everything()
twin.started, twin.path = taken.started, taken.path
twin.open()
check(twin.path != taken.path and taken.path.exists() and twin.path.exists(),
      f"two recordings in the same second get their own files, got {twin.path.name}")
tap(session, VK_A, scan=30)
session.open(window=[0, 0, 100, 100], picture=types.SimpleNamespace(
    save=lambda path: Path(path).write_bytes(b"png")))
check(lines(session.path)[0]["window"] == [0, 0, 100, 100], "the header notes the game window")
check(record_all.shot_path(session.path).read_bytes() == b"png",
      "the start picture is saved next to the recording")
check(lines(session.path)[0]["start_shot"] == record_all.shot_path(session.path).name,
      "the header names the start picture")
session.flush()
tap(session, VK_W, scan=17)
session.flush()
session.flush()
check(len(lines(session.path)) == 5, f"each event is streamed once, got {len(lines(session.path))}")
session.events.insert(0, {"t": 99.0, "kind": "key", "action": "up", "vk": 1, "scan": 0,
                          "extended": False, "name": "", "repeat": False})
said = session.finish({})
ts = [e["t"] for e in lines(session.path)[1:]]
check(ts == sorted(ts), "the final file is in time order")
check(said[-1] == f"saved {session.path}" and any("key down: 2" in l for l in said),
      f"the report counts events and names the file, got {said}")
idle = record_all.Everything(armed=True)
check(idle.finish({}) == ["stopped before recording began; nothing saved"],
      "stopping before the start saves nothing")

# ---- replay: what it refuses ----

def prepared(path):
    return record_all.prepare(path)


empty = SCRATCH / "empty_all.jsonl"
empty.write_text("", encoding="utf-8")
check("empty" in (prepared(empty)[2] or ""), "an empty file is refused")
headless = write(SCRATCH / "headless_all.jsonl", {"kind": "key"}, [])
check("no start line" in (prepared(headless)[2] or ""), "a file with no header is refused")
eventless = write(SCRATCH / "eventless_all.jsonl", HEAD, [])
check("holds no events" in (prepared(eventless)[2] or ""), "a file with no events is refused")
a_down = {"t": 0.2, "kind": "key", "action": "down", "vk": VK_A, "scan": 30,
          "extended": False, "name": "A", "repeat": False, "injected": False}
a_up = {**a_down, "t": 0.1, "action": "up"}
crashed = write(SCRATCH / "crashed_all.jsonl", HEAD, [a_down, a_up], tail='{"t": 0.3, "ki')
head, events, refused = prepared(crashed)
check(refused is None and len(events) == 2, f"a file cut off mid-line still plays, got {refused}")
check([e["t"] for e in events] == [0.1, 0.2], "events are played in time order")
elsewhere = write(SCRATCH / "elsewhere_all.jsonl", {**HEAD, "screen": [1, 1]}, [a_down])
check("would not line up" in (prepared(elsewhere)[2] or ""), "a different screen is refused")
moved_desk = write(SCRATCH / "desk_all.jsonl", {**HEAD, "desktop": [0, 0, 1, 1]}, [a_down])
check("would not line up" in (prepared(moved_desk)[2] or ""),
      "a different monitor layout is refused")
in_game = write(SCRATCH / "game_all.jsonl", {**HEAD, "window": [0, 0, 800, 600]}, [a_down])
check("no PlayCabal window" in (prepared(in_game)[2] or ""), "a closed game is refused")
record_all.game_rect = lambda: [10, 0, 810, 600]
check("off by the difference" in (prepared(in_game)[2] or ""), "a moved game window is refused")
record_all.game_rect = lambda: [0, 0, 800, 600]
check(prepared(in_game)[2] is None, "the game where it was is accepted")
record_all.game_rect = lambda: None
secret = {"t": 0.1, "kind": "key", "action": "down", "vk": 231, "secret": "password", "i": 0}
needs = write(SCRATCH / "needs_all.jsonl", HEAD, [secret])
saved_account, saved_env = record_all.ACCOUNT, os.environ.pop("CABAL_PASSWORD", None)
record_all.ACCOUNT = SCRATCH / "no_account.json"
try:
    check("password" in (prepared(needs)[2] or ""), "a missing password is refused")
    os.environ["CABAL_PASSWORD"] = "env-pw"
    check(prepared(needs)[2] is None, "the password from the environment is enough")
    os.environ.pop("CABAL_PASSWORD")
    record_all.ACCOUNT.write_text(json.dumps({"password": "file-pw"}), encoding="utf-8")
    check(record_all.credentials()["password"] == "file-pw", "the account file is read")
    record_all.ACCOUNT.write_text("{not json", encoding="utf-8")
    check(record_all.credentials()["password"] is None, "a broken account file reads as empty")
finally:
    record_all.ACCOUNT = saved_account
    if saved_env is not None:
        os.environ["CABAL_PASSWORD"] = saved_env

# ---- replay: what it sends ----

sent = []
real_send = ra.send


def as_point(item):
    mi = item.u.mi
    left, top, width, height = ra.desktop()
    return (left + mi.dx * width // W["ABSOLUTE_SPAN"],
            top + mi.dy * height // W["ABSOLUTE_SPAN"])


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
        {"t": 0.0045, "kind": "key", "action": "down", "vk": 231,
         "secret": "password", "i": 9, "injected": True},
        {"t": 0.005, "kind": "mouse", "action": "move", "x": 7, "y": 8,
         "source": "poll"},
        {"t": 0.006, "kind": "mouse", "action": "down", "button": "left",
         "x": 7, "y": 8, "injected": True},
        {"t": 0.007, "kind": "key", "action": "down", "vk": VK_W, "scan": 17,
         "extended": False, "name": "W", "repeat": False},
    ]
    player = record_all.Replay(events, [5, 5], {"password": "xy"})
    player.play()
    check(player.finished and player.played == len(events),
          f"every event is played, got {player.played}")
    check(sent[0].type == W["INPUT_MOUSE"] and as_point(sent[0]) == (5, 5),
          "the cursor goes to the start position first")
    unicode = [item.u.ki for item in sent if item.type == W["INPUT_KEYBOARD"]
               and item.u.ki.dwFlags & record_all.UNICODE]
    check([(k.wScan, bool(k.dwFlags & W["KEYEVENTF_KEYUP"])) for k in unicode]
          == [(ord("y"), False), (ord("y"), True)],
          "only the password's known character is typed, from the account")
    tail = sent[-2:]
    check(any(i.type == W["INPUT_KEYBOARD"] and i.u.ki.wScan == 17
              and i.u.ki.dwFlags & W["KEYEVENTF_KEYUP"] for i in tail),
          "a key still held at the end is let go")
    check(any(i.type == W["INPUT_MOUSE"] and i.u.mi.dwFlags & W["MOUSEEVENTF_LEFTUP"]
              for i in tail), "a button still held at the end is let go")
    check(len(player.late) == len(events) and all(late >= 0 for late in player.late),
          "every event's lateness is measured")
    check("after its recorded moment" in player.timing(), "the timing report reads")
    check(record_all.Replay([], None, {}).timing() == "no events were sent",
          "an empty replay reports nothing sent")

    # Stopping part way still lets go of everything held.
    sent.clear()
    long_events = [{**a_down, "t": 0.0, "vk": VK_W, "scan": 17},
                   {"t": 0.0, "kind": "mouse", "action": "down", "button": "right",
                    "x": 9, "y": 9},
                   {**a_down, "t": 30.0}]
    player = record_all.Replay(long_events, None, {})
    worker = threading.Thread(target=player.play)
    worker.start()
    deadline = time.time() + 5
    while player.played < 2 and time.time() < deadline:
        time.sleep(0.01)
    player.stop.set()
    worker.join(5)
    check(not worker.is_alive() and player.played == 2,
          f"stopping ends the replay early, played {player.played}")
    check(any(i.type == W["INPUT_KEYBOARD"] and i.u.ki.dwFlags & W["KEYEVENTF_KEYUP"]
              for i in sent[-2:])
          and any(i.type == W["INPUT_MOUSE"] and i.u.mi.dwFlags & W["MOUSEEVENTF_RIGHTUP"]
                  for i in sent[-2:]),
          "a stopped replay lets go of the held key and button")

    # A recording made in the game needs the game in front, or nothing is sent.
    sent.clear()
    saved_focus = record_all.focus_game
    record_all.focus_game = lambda: False
    player = record_all.Replay([a_down], [1, 1], {}, {"window": [0, 0, 10, 10]})
    player.play()
    check(player.refused and sent == [] and player.played == 0,
          "if the game can't be brought to the front, nothing is sent")
    record_all.focus_game = lambda: True
    pictures = SCRATCH / "pics"
    pictures.mkdir()
    from PIL import Image
    Image.new("RGB", (32, 18), (0, 0, 0)).save(pictures / "start.png")
    saved_grab = record_all.grab
    record_all.grab = lambda rect: Image.new("RGB", (32, 18), (255, 255, 255))
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        player = record_all.Replay([a_down], None, {},
                                   {"window": [0, 0, 10, 10], "start_shot": "start.png"},
                                   pictures)
        player.play()
    check(player.played == 1 and "differs from the recording's start by 100%" in said.getvalue(),
          f"the start picture is compared before playing, got {said.getvalue()!r}")
    record_all.focus_game, record_all.grab = saved_focus, saved_grab
finally:
    ra.send = real_send

# ---- the start picture comparison ----

black = Image.new("RGB", (320, 180), (0, 0, 0))
check(record_all.difference(black, black) == 0.0, "identical pictures differ by 0")
half = black.copy()
half.paste((255, 255, 255), (0, 0, 160, 180))
check(abs(record_all.difference(black, half) - 0.5) < 0.02,
      "half the picture changed reads as about 50%")

# ---- positions land on the same pixel on this machine's monitors ----

saved_desktop = ra.desktop
for layout in ([0, 0, 1920, 1080], [0, 0, 5360, 1440], [-2560, 0, 4480, 1440]):
    ra.desktop = lambda layout=layout: layout
    left, top, width, height = layout
    off = []
    for x in range(left, left + width, 7):
        for y in (top, top + height // 2, top + height - 1):
            dx, dy = ra.absolute(x, y)
            back = (left + dx * width // W["ABSOLUTE_SPAN"],
                    top + dy * height // W["ABSOLUTE_SPAN"])
            if back != (x, y):
                off.append(((x, y), back))
    check(not off, f"every pixel maps back to itself on {layout}, first off {off[:3]}")
ra.desktop = saved_desktop

# ---- record then replay gives back the same input ----

ra.send = lambda item: sent.append(item)
sent.clear()
try:
    with fake_clock() as clock:
        session = record_all.Everything()
        session.open()
        script = []
        for step in range(40):
            clock.step(0.002)
            x, y = 100 + step * 3, 200 + step
            mouse(session, W["WM_MOUSEMOVE"], x, y)
            script.append(("move", (x, y)))
        clock.step(0.05)
        mouse(session, W["WM_LBUTTONDOWN"], 220, 240)
        clock.step(0.05)
        mouse(session, W["WM_LBUTTONUP"], 220, 240)
        clock.step(0.01)
        key(session, VK_W, True, scan=17)
        clock.step(0.2)
        key(session, VK_W, False, scan=17)
        session.finish({})
    head, events, refused = prepared(session.path)
    check(refused is None, f"the new recording is accepted, got {refused}")
    player = record_all.Replay(events, head["cursor"], {})
    player.play()
    played = sent[1:]
    moves = [as_point(i) for i in played
             if i.type == W["INPUT_MOUSE"] and not i.u.mi.dwFlags & (
                 W["MOUSEEVENTF_LEFTDOWN"] | W["MOUSEEVENTF_LEFTUP"])]
    check(moves == [p for kind, p in script], "every cursor move replays to the same pixel")
    clicks = [(as_point(i), i.u.mi.dwFlags & (W["MOUSEEVENTF_LEFTDOWN"] | W["MOUSEEVENTF_LEFTUP"]))
              for i in played if i.type == W["INPUT_MOUSE"]
              and i.u.mi.dwFlags & (W["MOUSEEVENTF_LEFTDOWN"] | W["MOUSEEVENTF_LEFTUP"])]
    check(clicks == [((220, 240), W["MOUSEEVENTF_LEFTDOWN"]),
                     ((220, 240), W["MOUSEEVENTF_LEFTUP"])],
          f"the click replays at the same spot, got {clicks}")
    keys = [(i.u.ki.wScan, bool(i.u.ki.dwFlags & W["KEYEVENTF_KEYUP"]))
            for i in played if i.type == W["INPUT_KEYBOARD"]]
    check(keys == [(17, False), (17, True)], f"the key replays down then up, got {keys}")
    check(abs(events[0]["t"] - 0.002) < 1e-6 and abs(events[-1]["t"] - 0.39) < 1e-6,
          f"the recording keeps the original moments, got {events[0]['t']}..{events[-1]['t']}")
    check(max(player.late) < 0.05, f"replay keeps time, worst {max(player.late):.4f}s late")
finally:
    ra.send = real_send

# ---- the command line ----

check(record_all.main(["nonsense"]) == record_all.USAGE, "unknown arguments print usage")
check(record_all.main(["run"]) == record_all.USAGE, "run with no command prints usage")
check(record_all.main(["replay", "a", "b"]) == record_all.USAGE, "replay takes one file")
saved_out = ra.OUT
ra.OUT = SCRATCH / "nothing_here"
with contextlib.redirect_stdout(io.StringIO()):
    check(record_all.main(["replay"]) == 1, "replay with no recordings fails cleanly")
    check(record_all.main(["replay", str(SCRATCH / "missing_all.jsonl")]) == 1,
          "replay of a missing file fails cleanly")
ra.OUT = saved_out

# ---- recording while a command runs ----

saved_hook, saved_pump = ra.hook, ra.pump
ra.hook = lambda *a, **k: (None, None)


def pump(done, tick):
    while not done():
        time.sleep(0.01)
        tick()


ra.pump = pump
try:
    with contextlib.redirect_stdout(io.StringIO()):
        code = record_all.record([sys.executable, "-c", "import sys; sys.exit(3)"])
        missing = record_all.record(["no-such-program-xyz"])
finally:
    ra.hook, ra.pump = saved_hook, saved_pump
check(code == 3, f"run mode returns the command's exit code, got {code}")
check(missing == 1, f"a command that can't start fails, got {missing}")
heads = [json.loads(p.read_text(encoding="utf-8").splitlines()[0])
         for p in SCRATCH.glob(f"20*{record_all.SUFFIX}")]
check(any(h.get("command") and h["command"][0] == sys.executable for h in heads),
      "the recording notes the command it ran")

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
