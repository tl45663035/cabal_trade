"""A pretend Windows input system and a pretend game, for driving the real
recorder (tools/record_all.py) and the real replayers end to end with no
real input.

FakeInput stands in for the parts of Windows the recorder and replayer touch:
the cursor (pointer speed, sub-pixel remainder, edges), the low-level
keyboard and mouse hooks, raw mouse counts, SetCursorPos (which no hook sees)
and SendInput. Game logs what a game would be told: keys by scan code,
typed characters, the cursor, buttons, wheel turns, and camera turns from raw
counts while the right button is held.

A session is played through the real record(), the file it saves is replayed
through the real Replay, and the two Game logs are compared on one
microsecond clock."""

import bisect
import contextlib
import json
import math
import threading
import types
from fractions import Fraction

import record_action as ra
import record_all

W = ra.W
SPAN = W["ABSOLUTE_SPAN"]
MEASURED = json.loads((__import__("pathlib").Path(__file__).parent
                       / "windows_absolute_5360x1440.json").read_text(encoding="utf-8"))


_RUNS = {axis: ([start for start, _pixel in MEASURED[axis]],
                [pixel for _start, pixel in MEASURED[axis]]) for axis in ("x", "y")}


def windows_absolute(n, size, axis):
    """Where Windows puts an absolute (VIRTUALDESK) value on an axis of size
    pixels. On the 5360x1440 desktop it is what was measured on the real
    machine; on any other, the same kind of coarse grid: some pixels are
    never reached by an absolute move."""
    left, top, width, height = MEASURED["desktop"]
    if (axis == "x" and size == width) or (axis == "y" and size == height):
        starts, pixels = _RUNS[axis]
        return pixels[bisect.bisect_right(starts, n) - 1]
    grid = size * 4888 // 5360
    return (n * grid // SPAN) * size // grid

# name: (vk, scan, extended)
KEYS = {
    "W": (87, 17, False), "A": (65, 30, False), "S": (83, 31, False),
    "D": (68, 32, False), "Q": (81, 16, False), "E": (69, 18, False),
    "Z": (90, 44, False), "I": (73, 23, False), "1": (49, 2, False),
    "2": (50, 3, False), "0": (48, 11, False), "9": (57, 10, False),
    "Esc": (27, 1, False), "Enter": (13, 28, False), "NumEnter": (13, 28, True),
    "Space": (32, 57, False), "Tab": (9, 15, False), "Backspace": (8, 14, False),
    "Up": (38, 72, True), "Down": (40, 80, True), "Left": (37, 75, True),
    "Right": (39, 77, True), "Delete": (46, 83, True), "End": (35, 79, True),
    "F1": (112, 59, False), "F10": (121, 68, False), "Slash": (191, 53, False),
    "Ctrl": (162, 29, False), "RCtrl": (163, 29, True), "Shift": (160, 42, False),
    "Alt": (164, 56, False), "Win": (91, 91, True),
}
BY_SCAN = {(scan, ext): vk for vk, scan, ext in KEYS.values()}
BY_VK = {vk: (scan, ext) for vk, scan, ext in KEYS.values()}


def scan_to_vk(scan, ext):
    """Keys outside the table are looked up the way Windows does (a read-only
    call: MapVirtualKey sends nothing)."""
    if (scan, ext) in BY_SCAN:
        return BY_SCAN[(scan, ext)]
    return ra.ctypes.windll.user32.MapVirtualKeyW(scan | (0xE000 if ext else 0), 3)


def vk_to_scan(vk):
    if vk in BY_VK:
        return BY_VK[vk][0]
    return ra.ctypes.windll.user32.MapVirtualKeyW(vk, 0)
ALT = {164, 165, 18}
BUTTONS = {
    "left": (W["WM_LBUTTONDOWN"], W["WM_LBUTTONUP"],
             W["MOUSEEVENTF_LEFTDOWN"], W["MOUSEEVENTF_LEFTUP"]),
    "right": (W["WM_RBUTTONDOWN"], W["WM_RBUTTONUP"],
              W["MOUSEEVENTF_RIGHTDOWN"], W["MOUSEEVENTF_RIGHTUP"]),
    "middle": (W["WM_MBUTTONDOWN"], W["WM_MBUTTONUP"],
               W["MOUSEEVENTF_MIDDLEDOWN"], W["MOUSEEVENTF_MIDDLEUP"]),
    "x1": (W["WM_XBUTTONDOWN"], W["WM_XBUTTONUP"],
           W["MOUSEEVENTF_XDOWN"], W["MOUSEEVENTF_XUP"]),
    "x2": (W["WM_XBUTTONDOWN"], W["WM_XBUTTONUP"],
           W["MOUSEEVENTF_XDOWN"], W["MOUSEEVENTF_XUP"]),
}
X_DATA = {"x1": W["XBUTTON1"], "x2": W["XBUTTON2"]}


class Game:
    """What the game is told, stamped with the shared clock (microseconds)."""

    def __init__(self, cursor):
        self.log = []
        self.soft = set()
        self.cursor = tuple(cursor)
        self.camera = [0, 0]
        self.keys = set()
        self.buttons = set()

    def key(self, t, scan, ext, up):
        (self.keys.discard if up else self.keys.add)((scan, ext))
        self.log.append((t, "key", (scan, ext, up)))

    def char(self, t, ch, up):
        self.log.append((t, "char", (ch, up)))

    def cursor_to(self, t, where, software=False):
        where = tuple(where)
        if where != self.cursor:
            self.cursor = where
            self.log.append((t, "move", where))
            if software:
                self.soft.add(t)

    def raw(self, t, dx, dy):
        if self.buttons & set(record_all.TURN):
            self.camera[0] += dx
            self.camera[1] += dy
            self.log.append((t, "turn", (dx, dy)))

    def button(self, t, name, down):
        (self.buttons.add if down else self.buttons.discard)(name)
        self.log.append((t, "button", (name, down, self.cursor)))

    def wheel(self, t, delta, horizontal):
        self.log.append((t, "wheel", (delta, horizontal, self.cursor)))


class FakeInput:
    def __init__(self, layout=(0, 0, 1920, 1080), primary=(1920, 1080),
                 cursor=(960, 540), speed=Fraction(3, 4), clock_us=1_000_000):
        self.layout = list(layout)
        self.primary = list(primary)
        self.cursor = list(cursor)
        self.speed = Fraction(speed)
        self.rem = [Fraction(0), Fraction(0)]
        self.clock = clock_us
        self.game = Game(cursor)
        self.key_hook = self.mouse_hook = self.raw_hook = None
        self.workers = []
        self.held = set()
        self.marks = {}
        self.sends = []
        self.raw_made = 0

    # -- time --
    def now(self):
        return self.clock / 1e6

    def wait(self, ms):
        self.clock += int(round(ms * 1000))

    def until(self, target, stop=None):
        if stop is not None and stop.is_set():
            return
        stop_at = self.marks.get("stop_replay_at")
        target_us = int(round(target * 1e6))
        if stop_at is not None and target_us >= stop_at and stop is not None:
            self.clock = max(self.clock, stop_at)
            stop.set()
            return
        self.clock = max(self.clock, target_us)

    # -- the cursor --
    def _clamp(self, x, y):
        left, top, width, height = self.layout
        return (min(max(x, left), left + width - 1),
                min(max(y, top), top + height - 1))

    def _relative(self, dx, dy):
        out, rem = [], []
        for axis, (move, start) in enumerate(((dx, self.cursor[0]),
                                              (dy, self.cursor[1]))):
            exact = move * self.speed + self.rem[axis]
            whole = math.trunc(exact)
            out.append(start + whole)
            rem.append(exact - whole)
        clamped = self._clamp(*out)
        rem = [Fraction(0) if clamped[i] != out[i] else rem[i] for i in range(2)]
        return clamped, rem

    def _absolute(self, dx, dy):
        left, top, width, height = self.layout
        return self._clamp(left + windows_absolute(dx, width, "x"),
                           top + windows_absolute(dy, height, "y"))

    # -- hooks --
    def _key_event(self, vk, scan, ext, up, injected):
        system = vk in ALT or bool(self.held & ALT)
        if system:
            message = W["WM_SYSKEYUP"] if up else W["WM_SYSKEYDOWN"]
        else:
            message = W["WM_KEYUP"] if up else W["WM_KEYDOWN"]
        flags = (W["LLKHF_EXTENDED"] if ext else 0) | (W["LLKHF_INJECTED"] if injected else 0)
        if self.key_hook is not None:
            self.key_hook(message, ra.KBDLLHOOKSTRUCT(vkCode=vk, scanCode=scan,
                                                      flags=flags))
        (self.held.discard if up else self.held.add)(vk)

    def _mouse_event(self, message, where, data, injected):
        if self.mouse_hook is not None:
            self.mouse_hook(message, ra.MSLLHOOKSTRUCT(
                pt=ra.wintypes.POINT(*where), mouseData=data & 0xFFFFFFFF,
                flags=W["LLMHF_INJECTED"] if injected else 0))

    def _raw(self, flags, dx, dy, physical):
        if self.raw_hook is not None:
            self.raw_hook(flags, dx, dy, physical)

    # -- the hand on the keyboard and mouse --
    def press(self, name, down=True):
        vk, scan, ext = KEYS[name]
        self._key_event(vk, scan, ext, not down, False)
        self.game.key(self.clock, scan, ext, not down)

    def release(self, name):
        self.press(name, False)

    def move(self, dx, dy):
        where, rem = self._relative(dx, dy)
        self._mouse_event(W["WM_MOUSEMOVE"], where, 0, False)
        self.cursor, self.rem = list(where), rem
        self.game.cursor_to(self.clock, where)
        if dx or dy:
            self.raw_made += 1
            self._raw(0, dx, dy, True)
            self.game.raw(self.clock, dx, dy)

    def button(self, name, down=True):
        message = BUTTONS[name][0 if down else 1]
        self._mouse_event(message, self.cursor, X_DATA.get(name, 0) << 16, False)
        self.game.button(self.clock, name, down)

    def wheel(self, delta, horizontal=False):
        message = W["WM_MOUSEHWHEEL"] if horizontal else W["WM_MOUSEWHEEL"]
        self._mouse_event(message, self.cursor, (delta & 0xFFFF) << 16, False)
        self.game.wheel(self.clock, delta, horizontal)

    # -- software: SetCursorPos (seen by no hook) and SendInput --
    def set_cursor(self, x, y):
        where = self._clamp(x, y)
        if self.game.buttons & set(record_all.TURN) and tuple(where) != tuple(self.cursor):
            self.game.log.append((self.clock, "jump", tuple(where)))
        self.cursor = list(where)
        self.rem = [Fraction(0), Fraction(0)]
        self.game.cursor_to(self.clock, self.cursor, software=True)

    def send(self, item):
        self.sends.append((self.clock, item))
        if item.type == W["INPUT_KEYBOARD"]:
            ki = item.u.ki
            up = bool(ki.dwFlags & W["KEYEVENTF_KEYUP"])
            if ki.dwFlags & W["KEYEVENTF_UNICODE"]:
                self._key_event(W["VK_PACKET"], ki.wScan, False, up, True)
                self.game.char(self.clock, chr(ki.wScan), up)
                return
            ext = bool(ki.dwFlags & W["KEYEVENTF_EXTENDEDKEY"])
            if ki.dwFlags & W["KEYEVENTF_SCANCODE"]:
                scan, vk = ki.wScan, scan_to_vk(ki.wScan, ext)
            else:
                vk, scan = ki.wVk, vk_to_scan(ki.wVk)
            self._key_event(vk, scan, ext, up, True)
            self.game.key(self.clock, scan, ext, up)
            return
        mi = item.u.mi
        flags = mi.dwFlags
        if flags & W["MOUSEEVENTF_MOVE"]:
            if flags & W["MOUSEEVENTF_ABSOLUTE"]:
                where, rem = self._absolute(mi.dx, mi.dy), [Fraction(0), Fraction(0)]
                if self.game.buttons & set(record_all.TURN) and tuple(where) != tuple(self.cursor):
                    # An absolute jump while the camera button is down: a
                    # game turning by the cursor or by raw input would jerk.
                    self.game.log.append((self.clock, "jump", tuple(where)))
                self._mouse_event(W["WM_MOUSEMOVE"], where, 0, True)
                self.cursor, self.rem = list(where), rem
                self.game.cursor_to(self.clock, where)
                self._raw(W["MOUSE_MOVE_ABSOLUTE"], mi.dx, mi.dy, False)
            else:
                where, rem = self._relative(mi.dx, mi.dy)
                self._mouse_event(W["WM_MOUSEMOVE"], where, 0, True)
                self.cursor, self.rem = list(where), rem
                self.game.cursor_to(self.clock, where)
                self._raw(0, mi.dx, mi.dy, False)
                self.game.raw(self.clock, mi.dx, mi.dy)
        data = ra.ctypes.c_ulong(mi.mouseData).value
        for name, (down_msg, up_msg, down_flag, up_flag) in BUTTONS.items():
            if name in X_DATA and data != X_DATA[name]:
                continue
            for flag, message, down in ((down_flag, down_msg, True),
                                        (up_flag, up_msg, False)):
                if flags & flag:
                    self._mouse_event(message, self.cursor, X_DATA.get(name, 0) << 16, True)
                    self.game.button(self.clock, name, down)
        for flag, horizontal in ((W["MOUSEEVENTF_WHEEL"], False),
                                 (W["MOUSEEVENTF_HWHEEL"], True)):
            if flags & flag:
                delta = ra.ctypes.c_long(mi.mouseData).value
                message = W["WM_MOUSEHWHEEL"] if horizontal else W["WM_MOUSEWHEEL"]
                self._mouse_event(message, self.cursor, (delta & 0xFFFF) << 16, True)
                self.game.wheel(self.clock, delta, horizontal)


# -- the bot's way of sending input, as the bot's own modules build it --

def bot_key(fake, name, up):
    vk, scan, ext = KEYS[name]
    flags = W["KEYEVENTF_SCANCODE"] | (W["KEYEVENTF_KEYUP"] if up else 0)
    if ext:
        flags |= W["KEYEVENTF_EXTENDEDKEY"]
    fake.send(ra.INPUT(type=W["INPUT_KEYBOARD"], u=ra.INPUTUNION(
        ki=ra.KEYBDINPUT(vk, scan, flags, 0, 0))))


def bot_tap(fake, name):
    bot_key(fake, name, False)
    fake.wait(30)
    bot_key(fake, name, True)


def bot_click_at(fake, x, y, button="left"):
    event = {"x": x, "y": y}
    fake.send(ra.mouse_input(event, ra.BUTTON_FLAGS[("down", button)]))
    fake.wait(40)
    fake.send(ra.mouse_input(event, ra.BUTTON_FLAGS[("up", button)]))


def bot_click_here(fake, button="left"):
    fake.send(record_all.button_input(button, "down"))
    fake.wait(40)
    fake.send(record_all.button_input(button, "up"))


def typed(ch, up):
    flags = W["KEYEVENTF_UNICODE"] | (W["KEYEVENTF_KEYUP"] if up else 0)
    return ra.INPUT(type=W["INPUT_KEYBOARD"], u=ra.INPUTUNION(
        ki=ra.KEYBDINPUT(0, ord(ch), flags, 0, 0)))


def bot_type(fake, text):
    """The bot's relog types the account this way (recovery._type)."""
    for ch in text:
        fake.send(typed(ch, False))
        fake.send(typed(ch, True))
        fake.wait(20)


# -- scripted sessions --

class Script:
    """Steps of (wait in ms, what happens). Hand sessions start and stop with
    Ctrl pressed twice, as a person would."""

    def __init__(self):
        self.steps = []

    def at(self, ms, action):
        self.steps.append((ms, action))
        return self

    def tap(self, name, hold=40, gap=60, repeats=0, repeat_every=33):
        self.at(gap, lambda f: f.press(name))
        for _ in range(repeats):
            self.at(repeat_every, lambda f: f.press(name))
        return self.at(hold, lambda f: f.release(name))

    def combo(self, modifier, name, gap=60):
        self.at(gap, lambda f: f.press(modifier))
        self.tap(name, gap=30)
        return self.at(30, lambda f: f.release(modifier))

    def wander(self, counts, every=2):
        for dx, dy in counts:
            self.at(every, lambda f, dx=dx, dy=dy: f.move(dx, dy))
        return self

    def click(self, button="left", hold=60, gap=50):
        self.at(gap, lambda f: f.button(button, True))
        return self.at(hold, lambda f: f.button(button, False))

    def turn(self, counts, every=2, button="right", gap=40):
        self.at(gap, lambda f: f.button(button, True))
        self.wander(counts, every)
        return self.at(every, lambda f: f.button(button, False))

    def wheel(self, delta, gap=50, horizontal=False):
        return self.at(gap, lambda f: f.wheel(delta, horizontal))

    def hover(self, x, y, gap=50):
        return self.at(gap, lambda f: f.set_cursor(x, y))

    def bot(self, action, gap=50):
        return self.at(gap, action)

    def ctrl_twice(self, mark):
        def first(f):
            if mark == "stop":
                f.marks["stop"] = f.clock
            f.press("Ctrl")
        self.at(300, first)
        self.at(40, lambda f: f.release("Ctrl"))
        self.at(100, lambda f: f.press("Ctrl"))

        def last(f):
            f.release("Ctrl")
            if mark == "start":
                f.marks["start"] = f.clock
        return self.at(40, last)


class SyncThread:
    """record_all's own threads, run in line, so the whole session is
    deterministic on the fake clock."""

    def __init__(self, target=None, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False


@contextlib.contextmanager
def patched(*changes):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _value in changes]
    try:
        for obj, name, value in changes:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)


def _layout_patches(fake, window=None, focus=True):
    return [(ra, "screen", lambda: list(fake.primary)),
            (ra, "desktop", lambda: list(fake.layout)),
            (record_all, "game_rect", lambda: window),
            (record_all, "focus_game", lambda: focus),
            (record_all, "cursor_now", lambda: tuple(fake.cursor)),
            (record_all, "set_cursor", fake.set_cursor),
            (record_all, "grab", lambda rect: _picture()),
            (record_all, "time", types.SimpleNamespace(perf_counter=fake.now,
                                                       sleep=lambda s: None)),
            (record_all, "threading", types.SimpleNamespace(
                Thread=SyncThread, Event=threading.Event, Lock=threading.Lock))]


def _picture():
    from PIL import Image
    return Image.new("RGB", (16, 9), (40, 40, 40))


class FakeChild:
    def __init__(self):
        self.done = False

    def poll(self):
        return 0 if self.done else None

    def wait(self):
        return 0


def record_session(script, out, fake=None, command=None, crash=False, window=None):
    """Plays script on the fake hand and through the real record_all.record().
    Returns the saved file and the fake input system it ran on."""
    fake = fake or FakeInput()
    child = FakeChild()
    if command:
        fake.marks["start"] = fake.clock

    def hook(kind, handler, flag, struct):
        if kind == W["WH_KEYBOARD_LL"]:
            fake.key_hook = handler
        else:
            fake.mouse_hook = handler
        return None, None

    def listen(session):
        fake.raw_hook = session.raw
        return None

    def every(gap, stop, work):
        fake.workers.append(work)
        return SyncThread()

    poll_ms = record_all.POLL * 1000

    def pump(done, tick):
        # The cursor check runs every POLL: it sees where a step leaves the
        # cursor unless the next step moves it again sooner than that.
        tick()
        steps = script.steps
        for n, (ms, action) in enumerate(steps):
            if done():
                break
            fake.wait(ms)
            action(fake)
            following = steps[n + 1][0] if n + 1 < len(steps) else float("inf")
            for k, work in enumerate(fake.workers):
                if k == 0 and following < poll_ms:
                    continue
                work()
            tick()
        child.done = True

    finished = record_all.Everything.finish
    changes = _layout_patches(fake, window) + [
        (ra, "OUT", out), (ra, "hook", hook), (ra, "pump", pump),
        (ra, "cursor_position", lambda: list(fake.cursor)),
        (record_all, "listen_raw", listen), (record_all, "_every", every),
        (record_all, "subprocess", types.SimpleNamespace(Popen=lambda cmd: child)),
        (ra, "Signal", lambda: types.SimpleNamespace(
            paint=lambda *a, **k: None, close=lambda: None)),
    ]
    if crash:
        changes.append((record_all.Everything, "finish",
                        lambda self: ["crashed"]))
    before = set(out.glob(f"*{record_all.SUFFIX}"))
    with patched(*changes):
        record_all.record(command)
    made = sorted(set(out.glob(f"*{record_all.SUFFIX}")) - before)
    assert len(made) == 1, made
    del finished
    return made[0], fake


def replay_session(path, entry="direct", fake=None, stop_at_ms=None, capture=None,
                   dungeon=None, window=None, focus=True, times=1, rest=0):
    """Replays path through one of the real entry points onto a fresh fake
    input system. capture, if given, is an Everything that listens to the
    hooks the way a run-mode recorder would."""
    fake = fake or FakeInput(cursor=(3, 3))
    fake.marks["zeros"] = []
    if capture is not None:
        fake.key_hook, fake.mouse_hook, fake.raw_hook = (
            capture.key, capture.mouse, capture.raw)

    class Taps:
        def __init__(self, own=None, state=None):
            pass

        def step(self, now):
            return True

    real_play = record_all.Replay.play

    def play(player):
        fake.marks["zeros"].append(fake.clock)
        return real_play(player)

    changes = _layout_patches(fake, window, focus) + [
        (ra, "send", fake.send),
        (ra, "wait_until", fake.until),
        (record_all, "CtrlTaps", Taps),
        (record_all.Replay, "play", play),
    ]
    with patched(*changes):
        if entry == "direct":
            head, events, refused = record_all.prepare(path)
            fake.marks["refused"] = refused
            if refused is None:
                player = record_all.Replay(events, head.get("cursor"), head,
                                           path.parent)
                _arm_stop(fake, stop_at_ms)
                player.play()
                fake.marks["refused"] = player.refused
        elif entry == "cli":
            _arm_stop(fake, stop_at_ms)
            with contextlib.redirect_stdout(_Quiet()):
                fake.marks["code"] = record_all.main(["replay", str(path)])
        elif entry == "dungeon":
            _arm_stop(fake, stop_at_ms)
            with contextlib.redirect_stdout(_Quiet()):
                try:
                    fake.marks["done"] = dungeon.replay_recording(str(path), times, rest)
                except SystemExit as exc:
                    fake.marks["refused"] = str(exc)
        else:
            raise ValueError(entry)
    return fake


class _Quiet:
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def _arm_stop(fake, stop_at_ms):
    if stop_at_ms is None:
        fake.marks.pop("stop_replay_at", None)
        return
    # The replay's zero is the clock when it starts; stop that far in.
    fake.marks["stop_replay_at"] = fake.clock + int(stop_at_ms * 1000)


def replay_zero(fake):
    return fake.marks["zeros"][0]


# -- comparing what the game was told --

def window(log, zero, end=None):
    return [(t - zero, kind, data) for t, kind, data in log
            if t - zero > 0 and (end is None or t - zero < end)]


def _cursor_at(log, start):
    timeline, where = [], tuple(start)
    for t, kind, data in log:
        if kind == "move":
            where = data
        timeline.append((t, where))
    return timeline


def _sample(timeline, start, t):
    where = tuple(start)
    for at, pos in timeline:
        if at > t:
            break
        where = pos
    return where


def differences(recorded, replayed, rec_start, rep_start, tolerance=1, soft=(),
                slack_us=None):
    """Everything the game was told while recording vs while replaying.
    Keys, characters, buttons, wheel turns and camera turns must match
    exactly, at the same microsecond. The cursor must match exactly too,
    except while the camera turns, where Windows' sub-pixel remainder may
    leave it one pixel apart (and it is put back when the turn ends), and
    within one cursor check (slack_us) of a SetCursorPos move (soft, times
    relative to the recording's start), which no hook reports: the recorder
    can only see it on its next check."""
    if slack_us is None:
        slack_us = int(record_all.POLL * 1e6)
    soft = sorted(soft)
    found = []
    for kind in ("key", "turn", "jump"):
        a = [(t, d) for t, k, d in recorded if k == kind]
        b = [(t, d) for t, k, d in replayed if k == kind]
        if a != b:
            found.append(_first_gap(kind, a, b))
    for kind in ("button", "wheel"):
        a = [(t, d[:-1]) for t, k, d in recorded if k == kind]
        b = [(t, d[:-1]) for t, k, d in replayed if k == kind]
        if a != b:
            found.append(_first_gap(kind, a, b))
    rec_line = _cursor_at([e for e in recorded if e[1] == "move"], rec_start)
    rep_line = _cursor_at([e for e in replayed if e[1] == "move"], rep_start)
    turning = _turning(recorded)
    times = sorted({t for t, _k, _d in recorded} | {t for t, _k, _d in replayed}
                   | {at + slack_us for at in soft})
    for t in times:
        a = _sample(rec_line, rec_start, t)
        b = _sample(rep_line, rep_start, t)
        allowed = tolerance if _within(turning, t) else 0
        if any(0 <= t - at < slack_us for at in soft):
            continue
        if max(abs(a[0] - b[0]), abs(a[1] - b[1])) > allowed:
            found.append(f"cursor at {t}us: recorded {a}, replayed {b}"
                         f"{' (in a turn)' if allowed else ''}")
            break
    a = [(t, d) for t, k, d in recorded if k in ("button", "wheel")]
    b = [(t, d) for t, k, d in replayed if k in ("button", "wheel")]
    for (ta, da), (tb, db) in zip(a, b):
        allowed = tolerance if _within(turning, ta, ends=True) else 0
        pa, pb = da[-1], db[-1]
        if max(abs(pa[0] - pb[0]), abs(pa[1] - pb[1])) > allowed:
            found.append(f"{da[:-1]} at {ta}us landed at {pb}, recorded at {pa}")
            break
    return found


def _turning(log):
    """When any turn button (record_all.TURN) is held: the replay goes by raw
    counts then, so the cursor may sit one sub-pixel remainder apart."""
    spans, began, held = [], None, set()
    for t, kind, data in log:
        if kind == "button" and data[0] in record_all.TURN:
            (held.add if data[1] else held.discard)(data[0])
            if held and began is None:
                began = t
            elif not held and began is not None:
                spans.append((began, t))
                began = None
    if began is not None:
        spans.append((began, float("inf")))
    return spans


def _within(spans, t, ends=False):
    return any((start < t < end) or (ends and t == end) for start, end in spans)


def _first_gap(kind, a, b):
    for n, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"{kind} #{n}: recorded {x}, replayed {y}"
    return (f"{kind}: recorded {len(a)}, replayed {len(b)}; first extra "
            f"{(a[len(b)] if len(a) > len(b) else b[len(a)])}")
