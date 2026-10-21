import contextlib
import ctypes
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
    session.finish()
    saved = lines(session.path)
    check([e.get("name") for e in saved[1:]] == ["A", "A"],
          f"the stop gesture is dropped and the rest kept, got {saved[1:]}")
    check(saved[0]["kind"] == "start" and saved[0]["recorder"] == "record_all",
          f"header first, got {saved[0]}")

from fake_input import SyncThread
zooms = []
saved_zoom, saved_threading = record_all.zoom_out, record_all.threading
with fake_clock() as clock:
    def zooming(notches, sleep=None):
        zooms.append(notches)
        clock.step(0.6)
        mouse(session, W["WM_MOUSEWHEEL"], 960, 540, injected=True, data=120 << 16)

    record_all.zoom_out = zooming
    record_all.threading = types.SimpleNamespace(Thread=SyncThread, Event=threading.Event,
                                                 Lock=threading.Lock)
    try:
        session = record_all.Everything(armed=True, zoom=12)
        tap(session, CTRL)
        clock.step(0.1)
        tap(session, CTRL)
        check(zooms == [12] and session.recording,
              f"zoom: Ctrl x2 zooms out by the notches, then the recording starts, got "
              f"{zooms} {session.recording}")
        check(session.events == [], f"zoom: the zoom's wheel turns are not recorded, got "
                                    f"{session.events}")
        clock.step(0.25)
        tap(session, VK_A, scan=30)
        check(session.events and session.events[0]["t"] == 0.25,
              f"zoom: time counts from after the zoom, got {session.events[:1]}")
        check(session.header().get("zoom_out") == 12,
              f"zoom: the header says the recording began zoomed out, got {session.header()}")
        plain = record_all.Everything(armed=True)
        zooms.clear()
        tap(plain, CTRL)
        clock.step(0.1)
        tap(plain, CTRL)
        check(zooms == [] and plain.recording and not plain.header().get("zoom_out"),
              f"zoom: a recorder without zoom starts at once and says no zoom, got {zooms}")
    finally:
        record_all.zoom_out, record_all.threading = saved_zoom, saved_threading

wheels = []
saved_send = ra.send
ra.send = lambda item: wheels.append(item)
try:
    record_all.zoom_out(4, sleep=lambda s: None)
finally:
    ra.send = saved_send
check(len(wheels) == 4 and all(
          w.u.mi.dwFlags == W["MOUSEEVENTF_WHEEL"]
          and ctypes.c_long(w.u.mi.mouseData).value == record_all.ZOOM["delta"]
          and record_all.ZOOM["delta"] == -120 for w in wheels),
      f"zoom_out: that many wheel turns down (zooms Cabal's camera out: the only way "
      f"the hand ever scrolls in the recordings), with no position of their own")

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

# ---- text typed by software (a login) is never recorded ----

session = record_all.Everything()
session.open()
tap(session, VK_A, injected=True, scan=30)
for ch in "bob pw1":
    tap(session, record_all.VK_PACKET, injected=True, scan=ord(ch))
tap(session, VK_END, injected=True)
session.flush()
streamed = session.path.read_text(encoding="utf-8")
session.finish()
saved = lines(session.path)
check(all(e.get("vk") != record_all.VK_PACKET for e in saved[1:]),
      "no typed character is saved, streamed or final")
check("bob" not in streamed and '"p"' not in streamed and "pw1" not in streamed,
      "the stream holds no typed text")
check([e.get("vk") for e in saved[1:]] == [65, 65, VK_END, VK_END],
      f"key presses around the typing are kept, got {saved[1:]}")
check(not session.path.with_name(session.path.name + ".part").exists(),
      "the rewrite leaves no spare file")
check(not hasattr(record_all, "credentials") and not hasattr(record_all, "ACCOUNT"),
      "the recorder has no way to read the account")
source = (SRC.parent / "tools" / "record_all.py").read_text(encoding="utf-8")
check("account.json" not in source and "CABAL_PASSWORD" not in source,
      "nothing in the recorder or replayer reads logins")

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
said = session.finish()
ts = [e["t"] for e in lines(session.path)[1:]]
check(ts == sorted(ts), "the final file is in time order")
check(said[-1] == f"saved {session.path}" and any("key down: 2" in l for l in said),
      f"the report counts events and names the file, got {said}")
idle = record_all.Everything(armed=True)
check(idle.finish() == ["stopped before recording began; nothing saved"],
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
legacy = {"t": 0.1, "kind": "key", "action": "down", "vk": 231, "secret": "password", "i": 0}
older = write(SCRATCH / "older_all.jsonl", HEAD, [legacy, a_down])
check(prepared(older)[2] is None,
      "an older recording with typed characters in it is still accepted")

# ---- replay: what it sends ----

sent = []
real_send = ra.send


def as_point(item):
    mi = item.u.mi
    left, top, width, height = ra.desktop()
    return (left + mi.dx * width // W["ABSOLUTE_SPAN"],
            top + mi.dy * height // W["ABSOLUTE_SPAN"])


placed = []


def cursor_from_sends():
    """Where the cursor is: the last absolute move sent (every pixel reachable
    here), or the last exact placement after it."""
    for item in reversed(sent):
        if isinstance(item, tuple):
            return item[1:]
        if item.type == W["INPUT_MOUSE"] and item.u.mi.dwFlags & W["MOUSEEVENTF_ABSOLUTE"]:
            return as_point(item)
    return None


def exact_cursor(x, y):
    placed.append((x, y))
    sent.append(("set_cursor", x, y))


record_all.cursor_now = cursor_from_sends
record_all.set_cursor = exact_cursor
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
    player = record_all.Replay(events, [5, 5])
    player.play()
    check(player.finished and player.played == len(events),
          f"every event is played, got {player.played}")
    check(sent[0].type == W["INPUT_MOUSE"] and as_point(sent[0]) == (5, 5),
          "the cursor goes to the start position first")
    unicode = [item for item in sent if item.type == W["INPUT_KEYBOARD"]
               and item.u.ki.dwFlags & W["KEYEVENTF_UNICODE"]]
    keyed = [item.u.ki.wScan for item in sent if item.type == W["INPUT_KEYBOARD"]]
    check(unicode == [] and 0 not in keyed and 231 not in keyed,
          "typed characters in an older recording are never sent")
    tail = sent[-2:]
    check(any(i.type == W["INPUT_KEYBOARD"] and i.u.ki.wScan == 17
              and i.u.ki.dwFlags & W["KEYEVENTF_KEYUP"] for i in tail),
          "a key still held at the end is let go")
    check(any(i.type == W["INPUT_MOUSE"] and i.u.mi.dwFlags & W["MOUSEEVENTF_LEFTUP"]
              for i in tail), "a button still held at the end is let go")
    check(len(player.late) == len(events) and all(late >= 0 for late in player.late),
          "every event's lateness is measured")
    check("after its recorded moment" in player.timing(), "the timing report reads")
    check(record_all.Replay([], None).timing() == "no events were sent",
          "an empty replay reports nothing sent")

    # Stopping part way still lets go of everything held.
    sent.clear()
    long_events = [{**a_down, "t": 0.0, "vk": VK_W, "scan": 17},
                   {"t": 0.0, "kind": "mouse", "action": "down", "button": "right",
                    "x": 9, "y": 9},
                   {**a_down, "t": 30.0}]
    player = record_all.Replay(long_events, None)
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
    player = record_all.Replay([a_down], [1, 1], {"window": [0, 0, 10, 10]})
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
        player = record_all.Replay([a_down], None,
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

ra.send = lambda item: sent.append(item)
try:
    walk = [{"t": 0.0, "kind": "key", "action": "down", "vk": 87, "scan": 17,
             "extended": False, "name": "W", "repeat": False, "injected": False},
            {"t": 0.05, "kind": "key", "action": "up", "vk": 87, "scan": 17,
             "extended": False, "name": "W", "repeat": False, "injected": False}]

    def shape(item):
        if isinstance(item, tuple):
            return "set"
        if item.type == W["INPUT_KEYBOARD"]:
            return "key"
        flags = item.u.mi.dwFlags
        if flags & W["MOUSEEVENTF_WHEEL"]:
            return ("wheel", ctypes.c_long(item.u.mi.mouseData).value)
        return "abs" if flags & W["MOUSEEVENTF_ABSOLUTE"] else "mouse"

    sent.clear()
    record_all.Replay(walk, [400, 300], {"zoom_out": 3}).play()
    up = record_all.ZOOM["delta"]
    check([shape(i) for i in sent] == ["abs", ("wheel", up), ("wheel", up), ("wheel", up),
                                       "key", "key"],
          f"zoom on replay: the cursor goes to the start, the camera zooms out by the "
          f"recording's notches, then the recording plays; got {[shape(i) for i in sent]}")
    sent.clear()
    record_all.Replay(walk, [400, 300], {}).play()
    check([shape(i) for i in sent] == ["abs", "key", "key"],
          f"no zoom on replay for a recording that didn't begin zoomed out, got "
          f"{[shape(i) for i in sent]}")
finally:
    ra.send = real_send

# ---- the cursor lands on the exact pixel, even ones Windows' grid skips ----

from fake_input import windows_absolute
saved = (ra.send, record_all.cursor_now, record_all.set_cursor, ra.desktop)
ra.desktop = lambda: [0, 0, 5360, 1440]
where, log = [0, 0], []


def windows_send(item):
    log.append("absolute")
    mi = item.u.mi
    where[:] = [windows_absolute(mi.dx, 5360, "x"), windows_absolute(mi.dy, 1440, "y")]


def windows_set(x, y):
    log.append(("set", x, y))
    where[:] = [x, y]


ra.send, record_all.cursor_now, record_all.set_cursor = (
    windows_send, lambda: tuple(where), windows_set)
try:
    reach_x = {windows_absolute(n, 5360, "x") for n in range(65536)}
    reach_y = {windows_absolute(n, 1440, "y") for n in range(65536)}
    skipped = [x for x in range(5360) if x not in reach_x]
    check(skipped[:3] == [2, 6, 11], f"the measured grid skips pixels, got {skipped[:3]}")
    off = []
    for x, y in [(2, 2), (820, 760), (6, 1439), (5359, 0), (1077, 652), (0, 0)]:
        log.clear()
        record_all.place(x, y)
        if tuple(where) != (x, y) or log[0] != "absolute":
            off.append(((x, y), tuple(where), list(log)))
    check(not off, f"place: the absolute move goes out, then the cursor is exactly on "
                   f"the pixel; got {off}")
    log.clear()
    reachable = next(x for x in range(100, 5360) if x in reach_x)
    reachable_y = next(y for y in range(100, 1440) if y in reach_y)
    record_all.place(reachable, reachable_y)
    check(log == ["absolute"] and tuple(where) == (reachable, reachable_y),
          f"place: a pixel the grid reaches needs no correction, got {log}")
    log.clear()
    record_all.place(reachable, reachable_y)
    check(log == [], f"place: a cursor already on the pixel is left alone, got {log}")
finally:
    ra.send, record_all.cursor_now, record_all.set_cursor, ra.desktop = saved

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
        session.finish()
    head, events, refused = prepared(session.path)
    check(refused is None, f"the new recording is accepted, got {refused}")
    player = record_all.Replay(events, head["cursor"])
    player.play()
    played = sent[1:]
    moves = [as_point(i) for i in played
             if i.type == W["INPUT_MOUSE"] and not i.u.mi.dwFlags & (
                 W["MOUSEEVENTF_LEFTDOWN"] | W["MOUSEEVENTF_LEFTUP"])]
    check(moves == [p for kind, p in script] + [(220, 240)],
          f"every cursor move replays to the same pixel, the cursor is put on the "
          f"click's pixel before the press, and not moved again for the release "
          f"(it is already there); got {moves[-4:]}")
    clicks = []
    for n, i in enumerate(played):
        if i.type == W["INPUT_MOUSE"] and i.u.mi.dwFlags & (
                W["MOUSEEVENTF_LEFTDOWN"] | W["MOUSEEVENTF_LEFTUP"]):
            before = [j for j in played[:n] if j.type == W["INPUT_MOUSE"]
                      and j.u.mi.dwFlags & W["MOUSEEVENTF_ABSOLUTE"]]
            clicks.append((as_point(before[-1]),
                           i.u.mi.dwFlags & (W["MOUSEEVENTF_LEFTDOWN"] | W["MOUSEEVENTF_LEFTUP"]),
                           bool(i.u.mi.dwFlags & W["MOUSEEVENTF_ABSOLUTE"])))
    check(clicks == [((220, 240), W["MOUSEEVENTF_LEFTDOWN"], False),
                     ((220, 240), W["MOUSEEVENTF_LEFTUP"], False)],
          f"the click replays at the same spot: the cursor is put there, then the "
          f"button goes out on its own (no position of its own to round), got {clicks}")
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
saved_listen = record_all.listen_raw
record_all.listen_raw = lambda session: None


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
    record_all.listen_raw = saved_listen
check(code == 3, f"run mode returns the command's exit code, got {code}")
check(missing == 1, f"a command that can't start fails, got {missing}")
heads = [json.loads(p.read_text(encoding="utf-8").splitlines()[0])
         for p in SCRATCH.glob(f"20*{record_all.SUFFIX}")]
check(any(h.get("command") and h["command"][0] == sys.executable for h in heads),
      "the recording notes the command it ran")

# ---- raw mouse counts: recorded from the mouse itself, used for camera turns ----

session = record_all.Everything()
session.raw(0, 5, -3, True)
session.raw(0, 0, 0, True)
session.raw(0, 4, 4, False)
session.raw(W["MOUSE_MOVE_ABSOLUTE"], 30000, 30000, True)
raws = [e for e in session.events if e["action"] == "raw"]
check([(e["dx"], e["dy"]) for e in raws] == [(5, -3)],
      f"only the mouse's own non-zero relative counts are kept, got {raws}")
idle = record_all.Everything(armed=True)
idle.raw(0, 5, 5, True)
check(idle.events == [], "raw counts before the start are not kept")

M = W["MOUSEEVENTF_MOVE"]
ABS = W["MOUSEEVENTF_ABSOLUTE"]


def described(item):
    if isinstance(item, tuple):
        return ("set", item[1:], ())
    if item.type == W["INPUT_KEYBOARD"]:
        k = item.u.ki
        return ("key", k.wScan, bool(k.dwFlags & W["KEYEVENTF_EXTENDEDKEY"]),
                bool(k.dwFlags & W["KEYEVENTF_KEYUP"]))
    mi = item.u.mi
    flags = mi.dwFlags
    if flags & ABS:
        what = "abs"
        where = as_point(item)
    elif flags & M:
        what, where = "rel", (mi.dx, mi.dy)
    else:
        what, where = "button", None
    buttons = [name for name, flag in (("rdown", W["MOUSEEVENTF_RIGHTDOWN"]),
                                       ("rup", W["MOUSEEVENTF_RIGHTUP"]),
                                       ("ldown", W["MOUSEEVENTF_LEFTDOWN"]),
                                       ("lup", W["MOUSEEVENTF_LEFTUP"])) if flags & flag]
    return (what, where, tuple(buttons))


turn = [
    {"t": 0.000, "kind": "mouse", "action": "move", "x": 50, "y": 50, "injected": False},
    {"t": 0.001, "kind": "mouse", "action": "raw", "dx": 1, "dy": 0},
    {"t": 0.010, "kind": "mouse", "action": "down", "button": "right", "x": 50, "y": 50,
     "injected": False},
    {"t": 0.011, "kind": "mouse", "action": "move", "x": 58, "y": 49, "injected": False},
    {"t": 0.0112, "kind": "mouse", "action": "raw", "dx": 6, "dy": -1},
    {"t": 0.012, "kind": "mouse", "action": "move", "x": 70, "y": 47, "injected": False},
    {"t": 0.0122, "kind": "mouse", "action": "raw", "dx": 9, "dy": -2},
    {"t": 0.013, "kind": "mouse", "action": "move", "x": 72, "y": 47, "source": "poll"},
    {"t": 0.020, "kind": "mouse", "action": "up", "button": "right", "x": 72, "y": 47,
     "injected": False},
    {"t": 0.021, "kind": "mouse", "action": "move", "x": 80, "y": 60, "injected": False},
    {"t": 0.0212, "kind": "mouse", "action": "raw", "dx": 5, "dy": 9},
]
ra.send = lambda item: sent.append(item)
try:
    sent.clear()
    player = record_all.Replay(turn, None)
    player.play()
    got = [described(i) for i in sent]
    check(got == [("abs", (50, 50), ()),
                  ("button", None, ("rdown",)),
                  ("rel", (6, -1), ()),
                  ("rel", (9, -2), ()),
                  ("set", (72, 47), ()),
                  ("button", None, ("rup",)),
                  ("abs", (80, 60), ())],
          f"a right drag turns by the raw counts (the bot's own cursor jump "
          f"mid-turn still goes out, straight to its pixel), the cursor lands "
          f"back where it was, and "
          f"outside a drag positions are used. Got {got}")

    def lefty(item):
        if isinstance(item, tuple):
            return described(item)
        name, flags = described(item)[0], item.u.mi.dwFlags if item.type == W["INPUT_MOUSE"] else 0
        presses = tuple(n for n, f in (("ldown", W["MOUSEEVENTF_LEFTDOWN"]),
                                        ("lup", W["MOUSEEVENTF_LEFTUP"])) if flags & f)
        return (name, described(item)[1], presses)

    left_drag = [{**e, "button": "left"} if e.get("button") == "right" else e for e in turn]
    sent.clear()
    record_all.Replay(left_drag, None).play()
    got = [lefty(i) for i in sent]
    check(got == [("abs", (50, 50), ()), ("button", None, ("ldown",)),
                  ("rel", (6, -1), ()), ("rel", (9, -2), ()), ("set", (72, 47), ()),
                  ("button", None, ("lup",)), ("abs", (80, 60), ())],
          f"a left drag (steering or turning while held) also goes by the raw counts, "
          f"got {got}")
    tap = [{"t": 0.0, "kind": "mouse", "action": "raw", "dx": 1, "dy": 0},
           {"t": 0.01, "kind": "mouse", "action": "down", "button": "left", "x": 300, "y": 200,
            "injected": False},
           {"t": 0.05, "kind": "mouse", "action": "up", "button": "left", "x": 300, "y": 200,
            "injected": False}]
    sent.clear()
    record_all.Replay(tap, None).play()
    clicks = [(as_point(i) if described(i)[0] == "abs" else None, lefty(i)[2]) for i in sent]
    check(clicks == [((300, 200), ()), (None, ("ldown",)), (None, ("lup",))],
          f"a short left click still presses and lets go exactly where it was, got {clicks}")
    check(set(record_all.TURN) >= {"right", "left", "middle"},
          f"right, left and middle drags all go by raw counts, got {record_all.TURN}")

    sent.clear()
    no_raw = [e for e in turn if e["action"] != "raw"]
    record_all.Replay(no_raw, None).play()
    got = [described(i) for i in sent]
    check([g for g in got if g[0] == "rel"] == [] and ("abs", (70, 47), ()) in got,
          "an older recording without raw counts still drags by cursor positions")

    sent.clear()
    bot_drag = [{**e, "injected": True} if e.get("button") else e for e in turn]
    record_all.Replay(bot_drag, None).play()
    got = [described(i) for i in sent]
    check([g for g in got if g[0] == "rel"] == [],
          "the bot's own right click doesn't switch to raw counts")

    sent.clear()
    held = turn[:7] + [{"t": 30.0, "kind": "mouse", "action": "move", "x": 1, "y": 1,
                        "injected": False}]
    player = record_all.Replay(held, None)
    worker = threading.Thread(target=player.play)
    worker.start()
    deadline = time.time() + 5
    while player.played < 7 and time.time() < deadline:
        time.sleep(0.01)
    player.stop.set()
    worker.join(5)
    check(described(sent[-1]) == ("button", None, ("rup",)),
          f"stopping mid-turn lets go of the right button without moving, got "
          f"{described(sent[-1])}")

    # ---- special keys record and replay like any other key ----
    sent.clear()
    special = [("Esc", 27, 1, False, False), ("Enter", 13, 28, False, False),
               ("Num Enter", 13, 28, True, False), ("Up", 38, 72, True, False),
               ("Delete", 46, 83, True, False), ("F1", 112, 59, False, False),
               ("Alt", 164, 56, False, True), ("Win", 91, 91, True, False),
               ("Tab", 9, 15, False, False), ("Backspace", 8, 14, False, False)]
    with fake_clock() as clock:
        session = record_all.Everything()
        session.open()
        for name, vk, scan, extended, system in special:
            clock.step(0.01)
            key(session, vk, True, scan=scan, extended=extended, system=system)
            clock.step(0.01)
            key(session, vk, False, scan=scan, extended=extended, system=system)
        session.finish()
    head, events, refused = prepared(session.path)
    check(refused is None and len(events) == 2 * len(special),
          f"every special key is recorded, got {len(events or [])}")
    record_all.Replay(events, None).play()
    got = [described(i) for i in sent]
    want = []
    for name, vk, scan, extended, system in special:
        want += [("key", scan, extended, False), ("key", scan, extended, True)]
    check(got == want, f"special keys replay with their scan code and extended flag, "
                       f"got {got}")
finally:
    ra.send = real_send

# ---- Ctrl taps from the key state, not a hook ----

class Keys:
    def __init__(self):
        self.down = False

    def __call__(self):
        return self.down


state, own = Keys(), {"on": False}
taps = record_all.CtrlTaps(own=lambda: own["on"], state=state)


def press(at, hold=0.05):
    state.down = True
    first = taps.step(at)
    state.down = False
    second = taps.step(at + hold)
    return first or second


check(not press(10.0), "one tap is not enough")
check(press(10.3), "a second tap within the window counts")
check(not press(11.0) and not press(13.0), "taps further apart than the window don't")
state.down = True
check(not taps.step(20.0) and not taps.step(20.05) and not taps.step(20.5),
      "a key held down counts once, not every poll")
state.down = False
taps.step(20.6)
taps = record_all.CtrlTaps(own=lambda: own["on"], state=state)
own["on"] = True
state.down = True
check(not taps.step(30.0), "the replay's own Ctrl is not a tap")
state.down = False
taps.step(30.1)
own["on"] = False
state.down = True
check(not taps.step(30.15), "nor is the key state catching up just after it")
state.down = False
taps.step(30.16)
check(not press(30.3 + record_all.OWN_GRACE) and press(30.5 + record_all.OWN_GRACE),
      "after the grace, two real taps count again")
check(record_all.wait_for_taps(types.SimpleNamespace(step=lambda now: True), 0.001),
      "waiting returns once the taps come")
check(not record_all.wait_for_taps(types.SimpleNamespace(step=lambda now: False), 0.001,
                                   until=lambda: True),
      "waiting gives up when told to")

# ---- the replay knows when it holds Ctrl ----

ra.send = lambda item: sent.append(item)
try:
    calls = []
    ctrl_events = [
        {"t": 0.0, "kind": "key", "action": "down", "vk": 162, "scan": 29,
         "extended": False, "name": "Ctrl", "repeat": False},
        {"t": 0.001, "kind": "key", "action": "down", "vk": 49, "scan": 2,
         "extended": False, "name": "1", "repeat": False},
        {"t": 0.002, "kind": "key", "action": "up", "vk": 49, "scan": 2,
         "extended": False, "name": "1", "repeat": False},
        {"t": 0.003, "kind": "key", "action": "up", "vk": 162, "scan": 29,
         "extended": False, "name": "Ctrl", "repeat": False},
        {"t": 0.004, "kind": "key", "action": "down", "vk": 65, "scan": 30,
         "extended": False, "name": "A", "repeat": False},
        {"t": 0.005, "kind": "key", "action": "up", "vk": 65, "scan": 30,
         "extended": False, "name": "A", "repeat": False},
    ]
    seen_own = []
    player = record_all.Replay(ctrl_events, None, on_ctrl=calls.append)
    real_play_event = record_all.play_event

    def watching(event, *a, **k):
        seen_own.append((event.get("name"), event["action"], player.own_ctrl))
        return real_play_event(event, *a, **k)

    record_all.play_event = watching
    try:
        player.play()
    finally:
        record_all.play_event = real_play_event
    check(calls == [True, False], f"Ctrl held, then let go, is reported once each, got {calls}")
    check(seen_own[0] == ("Ctrl", "down", True) and seen_own[3] == ("Ctrl", "up", True)
          and seen_own[4] == ("A", "down", False),
          f"the replay marks its Ctrl before pressing it and clears it after, got {seen_own}")

    calls.clear()
    held_ctrl = ctrl_events[:2] + [{**ctrl_events[4], "t": 30.0}]
    player = record_all.Replay(held_ctrl, None, on_ctrl=calls.append)
    worker = threading.Thread(target=player.play)
    worker.start()
    deadline = time.time() + 5
    while player.played < 2 and time.time() < deadline:
        time.sleep(0.01)
    player.stop.set()
    worker.join(5)
    check(calls == [True, False], f"a stop while Ctrl is held still clears it, got {calls}")

    calls.clear()
    record_all.Replay(ctrl_events[4:], None, on_ctrl=calls.append).play()
    check(calls == [], "a replay that never presses Ctrl says nothing")

    # Timing is measured once the event has gone out.
    ra.send = lambda item: (sent.append(item), time.sleep(0.02))
    player = record_all.Replay(ctrl_events[4:5], None)
    player.play()
    check(player.late and player.late[0] >= 0.02,
          f"the lateness includes the send itself, got {player.late}")
finally:
    ra.send = real_send

# ---- replay from the command line: Ctrl taps start and stop, no hook ----

saved_taps, saved_hook = record_all.CtrlTaps, ra.hook
ra.hook = lambda *a, **k: (_ for _ in ()).throw(AssertionError("replay installed a hook"))


class Script:
    def __init__(self, own=None, state=None):
        self.calls = 0

    def step(self, now):
        self.calls += 1
        return self.calls == 1


record_all.CtrlTaps = Script
ra.send = lambda item: sent.append(item)
sent.clear()
cli_file = write(SCRATCH / "cli_all.jsonl", HEAD, [
    {**a_down, "t": 0.01}, {**a_down, "t": 0.02, "action": "up"}])
try:
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        code = record_all.main(["replay", str(cli_file)])
    check(code == 0 and "replayed 2 of 2 events" in said.getvalue(),
          f"replay starts on the taps and plays everything, got {code} {said.getvalue()!r}")
    check(len(sent) == 3, f"the start cursor and both events are sent, got {len(sent)}")

    class StopSoon(Script):
        def step(self, now):
            self.calls += 1
            return self.calls in (1, 3)

    record_all.CtrlTaps = StopSoon
    long_file = write(SCRATCH / "long_all.jsonl", HEAD, [
        {**a_down, "t": 0.0}, {**a_down, "t": 60.0, "action": "up"}])
    sent.clear()
    started = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()) as said:
        code = record_all.main(["replay", str(long_file)])
    check(time.monotonic() - started < 10 and "stopped after 1 of 2" in said.getvalue(),
          f"taps during the replay stop it, got {said.getvalue()!r}")
    check(any(i.type == W["INPUT_KEYBOARD"] and i.u.ki.dwFlags & W["KEYEVENTF_KEYUP"]
              for i in sent[-1:]), "the key held when stopped is let go")
finally:
    record_all.CtrlTaps, ra.hook, ra.send = saved_taps, saved_hook, real_send

# ---- compare: what the game was actually sent ----

def recorded(name, events, head=None):
    return write(SCRATCH / name, head or HEAD, events)


def ev(t, kind, action, **more):
    return {"t": t, "kind": kind, "action": action, **more}


original = [ev(0.5, "key", "down", vk=87), ev(0.9, "key", "up", vk=87),
            ev(1.0, "mouse", "move", x=10, y=10, injected=False),
            ev(1.1, "mouse", "down", button="left", x=10, y=10, injected=False),
            ev(1.2, "mouse", "up", button="left", x=10, y=10, injected=False),
            ev(2.0, "mouse", "down", button="right", x=50, y=50, injected=False),
            ev(2.01, "mouse", "raw", dx=5, dy=0),
            ev(2.3, "mouse", "up", button="right", x=60, y=50, injected=False),
            ev(3.0, "mouse", "wheel", delta=-120, x=10, y=10, injected=False),
            ev(4.0, "key", "down", vk=49), ev(4.1, "key", "up", vk=49)]
for e in original:
    if e["kind"] == "key":
        e["injected"] = False
orig_path = recorded("orig_all.jsonl", original)


def shifted(events, by, wobble=0.0, **change):
    out = []
    for n, e in enumerate(events):
        if e["action"] == "raw":
            continue
        moved = {**e, "t": e["t"] + by + (wobble if n % 2 else -wobble), "injected": True}
        out.append(moved)
    return out


same = recorded("same_cap.jsonl", shifted(original, 2.0, wobble=0.0005))
found = record_all.compare(orig_path, same)
check(found["matched"] == found["wanted"] == 9 and found["missing"] == found["extra"] == 0,
      f"a faithful capture matches every key, click and wheel turn, got {found}")
check(abs(found["offset"] - 2.0) < 1e-3 and found["jitter_ms"][2] < 1.01,
      f"the start offset is taken out and only the wobble is left, got {found}")
check(abs(found["drift_ms"]) < 1.5 and not found["clicks_off"],
      f"no drift and every click in place, got {found}")

lost = shifted(original, 1.0)
del lost[-2]
found = record_all.compare(orig_path, recorded("lost_cap.jsonl", lost))
check(found["missing"] == 1 and record_all._signature(found["first_missing"])
      == ("key", 49, "down"), f"a missing key is found and named, got {found}")

off = shifted(original, 1.0)
off[3] = {**off[3], "x": 14}
off[6] = {**off[6], "x": 75}
found = record_all.compare(orig_path, recorded("off_cap.jsonl", off))
check(len(found["clicks_off"]) == 1 and found["clicks_off"][0][1] == 4,
      f"a click 4px off is reported, got {found['clicks_off']}")
check(len(found["turn_ends_off"]) == 1,
      f"a turn's release elsewhere is reported apart from clicks, got {found}")

drifting = [{**e, "t": e["t"] * 1.01} for e in shifted(original, 0.0)]
found = record_all.compare(orig_path, recorded("drift_cap.jsonl", drifting))
check(found["drift_ms"] > 20, f"a capture running slow shows drift, got {found['drift_ms']}")

handy = shifted(original, 0.5) + [ev(1.5, "key", "down", vk=65, injected=False)]
found = record_all.compare(orig_path, recorded("hand_cap.jsonl", handy))
check(found["by_hand"] == 1 and found["extra"] == 0,
      f"keys pressed by hand during the replay are left out, got {found}")

found = record_all.compare(orig_path, orig_path)
check(found["missing"] == 0 and found["by_hand"] == 0,
      f"a capture with nothing sent by software is used whole, not called 'by hand', "
      f"got {found}")

lines_out = record_all.compare_report(orig_path, recorded("lost2_cap.jsonl", lost))
check(any("1 missing" in l for l in lines_out) and any("timing:" in l for l in lines_out),
      f"the report says what's missing and how the timing went, got {lines_out}")
check(record_all.compare_report(orig_path, empty) == [f"{empty.name} is empty; nothing to replay"],
      "comparing against an empty file says so")
with contextlib.redirect_stdout(io.StringIO()) as said:
    check(record_all.main(["compare", str(orig_path), str(same)]) == 0,
          "compare runs from the command line")
check("9 of 9" in said.getvalue(), f"and prints the report, got {said.getvalue()!r}")
with contextlib.redirect_stdout(io.StringIO()):
    check(record_all.main(["compare", str(orig_path), "nope_all.jsonl"]) == 1,
          "a missing capture fails cleanly")
check(record_all.main(["compare", str(orig_path)]) == record_all.USAGE,
      "compare needs two files")

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
