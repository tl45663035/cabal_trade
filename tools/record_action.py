import ctypes
import datetime
import json
import shutil
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src_1080p"
R = json.loads((SRC / "config.json").read_text(encoding="utf-8"))["tools"]["recorder"]
W = R["win"]
OUT = SRC / "logs" / R["folder"]
SUBMIT_FOLDER = R["submit_folder"]
TAPS = int(R["toggle_taps"])
WINDOW = float(R["toggle_window"])
PUMP_MS = int(R["pump_ms"])
TIMER_MS = int(R["timer_resolution_ms"])
SPIN_BELOW = float(R["spin_below"])
SCREEN_FPS = float(R["screen_fps"])
SCREEN_MONITOR = int(R["screen_monitor"])
SCREEN_FOLDER = R["screen_folder"]
SCREEN_INDEX = R["screen_index"]
BANNER = R["banner"]
GLYPHS = BANNER["glyphs"]
GLYPH_ROWS = len(next(iter(GLYPHS.values())))
ESC = chr(27)
CTRL ={W["VK_CONTROL"], W["VK_LCONTROL"], W["VK_RCONTROL"]}
CUT = W["VK_Z"]
KEY_DOWN = {W["WM_KEYDOWN"], W["WM_SYSKEYDOWN"]}
KEY_UP = {W["WM_KEYUP"], W["WM_SYSKEYUP"]}
BUTTON_MESSAGES = {
    W["WM_LBUTTONDOWN"]: ("down", "left"), W["WM_LBUTTONUP"]: ("up", "left"),
    W["WM_RBUTTONDOWN"]: ("down", "right"), W["WM_RBUTTONUP"]: ("up", "right"),
    W["WM_MBUTTONDOWN"]: ("down", "middle"), W["WM_MBUTTONUP"]: ("up", "middle"),
    W["WM_XBUTTONDOWN"]: ("down", "x"), W["WM_XBUTTONUP"]: ("up", "x"),
}
WHEELS = {W["WM_MOUSEWHEEL"]: "wheel", W["WM_MOUSEHWHEEL"]: "hwheel"}
BUTTON_FLAGS = {
    ("down", "left"): W["MOUSEEVENTF_LEFTDOWN"], ("up", "left"): W["MOUSEEVENTF_LEFTUP"],
    ("down", "right"): W["MOUSEEVENTF_RIGHTDOWN"], ("up", "right"): W["MOUSEEVENTF_RIGHTUP"],
    ("down", "middle"): W["MOUSEEVENTF_MIDDLEDOWN"],
    ("up", "middle"): W["MOUSEEVENTF_MIDDLEUP"],
    ("down", "x1"): W["MOUSEEVENTF_XDOWN"], ("up", "x1"): W["MOUSEEVENTF_XUP"],
    ("down", "x2"): W["MOUSEEVENTF_XDOWN"], ("up", "x2"): W["MOUSEEVENTF_XUP"],
}
X_DATA = {"x1": W["XBUTTON1"], "x2": W["XBUTTON2"]}
WHEEL_FLAGS = {"wheel": W["MOUSEEVENTF_WHEEL"], "hwheel": W["MOUSEEVENTF_HWHEEL"]}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
winmm = ctypes.WinDLL("winmm")

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM,
                              wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", INPUTUNION)]


assert ctypes.sizeof(INPUT) == W["INPUT_STRUCT_SIZE"]

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE,
                                     wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM,
                                  wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT,
                            ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.GetKeyNameTextW.argtypes = [ctypes.c_long, wintypes.LPWSTR, ctypes.c_int]
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def screen():
    return (user32.GetSystemMetrics(W["SM_CXSCREEN"]),
            user32.GetSystemMetrics(W["SM_CYSCREEN"]))


def desktop():
    return [user32.GetSystemMetrics(W["SM_XVIRTUALSCREEN"]),
            user32.GetSystemMetrics(W["SM_YVIRTUALSCREEN"]),
            user32.GetSystemMetrics(W["SM_CXVIRTUALSCREEN"]),
            user32.GetSystemMetrics(W["SM_CYVIRTUALSCREEN"])]


def cursor_position():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return [point.x, point.y]


def key_name(scan, extended):
    buf = ctypes.create_unicode_buffer(W["KEY_NAME_CHARS"])
    code = ((scan << W["KEY_NAME_SCAN_SHIFT"])
            | (int(extended) << W["KEY_NAME_EXTENDED_SHIFT"]))
    user32.GetKeyNameTextW(code, buf, W["KEY_NAME_CHARS"])
    return buf.value


class Gesture:
    def __init__(self):
        self.reset()

    def reset(self):
        self.taps, self.down_at, self.events = [], None, []

    def key(self, vk, down, now, event):
        if vk not in CTRL:
            self.reset()
            return False
        if down:
            if self.taps and now - self.taps[0] > WINDOW:
                self.reset()
            self.down_at = now
            if event is not None:
                self.events.append(event)
            return False
        if event is not None:
            self.events.append(event)
        if self.down_at is None:
            return False
        self.taps.append(self.down_at)
        self.down_at = None
        if len(self.taps) >= TAPS and now - self.taps[0] <= WINDOW:
            return True
        return False


class Recording:
    def __init__(self, segments=False):
        self.gesture = Gesture()
        self.held = set()
        self.events = []
        self.zero = None
        self.started = None
        self.session = None
        self.recording = False
        self.finished = False
        self.kept = False
        self.cursor = None
        self.segments = segments
        self.number = 0
        self.saved = []
        self.writers = []
        self.reports = []

    def stamp(self, now, event):
        return {"t": round(now - self.zero, 6), **event}

    def key(self, message, info):
        now = time.perf_counter()
        down = message in KEY_DOWN
        if not down and message not in KEY_UP:
            return
        vk = info.vkCode
        repeat = down and vk in self.held
        if down:
            self.held.add(vk)
        else:
            self.held.discard(vk)
        if self.segments and vk == CUT:
            self.gesture.reset()
            if down and not repeat:
                if self.recording:
                    self.cut()
                else:
                    self.begin(now)
            return
        event = None
        if self.recording:
            extended = bool(info.flags & W["LLKHF_EXTENDED"])
            event = self.stamp(now, {
                "kind": "key", "action": "down" if down else "up", "vk": vk,
                "scan": info.scanCode, "extended": extended,
                "name": key_name(info.scanCode, extended), "repeat": repeat})
            self.events.append(event)
        if repeat:
            return
        if self.gesture.key(vk, down, now, event):
            self.toggle(now)

    def mouse(self, message, info):
        now = time.perf_counter()
        x, y = info.pt.x, info.pt.y
        high = ctypes.c_short(info.mouseData >> W["HIGH_WORD_SHIFT"]).value
        if message in BUTTON_MESSAGES:
            action, button = BUTTON_MESSAGES[message]
            if button == "x":
                button = "x1" if high == W["XBUTTON1"] else "x2"
            self.gesture.reset()
            event = {"kind": "mouse", "action": action, "button": button,
                     "x": x, "y": y}
        elif message in WHEELS:
            event = {"kind": "mouse", "action": WHEELS[message], "delta": high,
                     "x": x, "y": y}
        elif message == W["WM_MOUSEMOVE"]:
            event = {"kind": "mouse", "action": "move", "x": x, "y": y}
        else:
            return
        if self.recording:
            self.events.append(self.stamp(now, event))

    def begin(self, now):
        self.recording, self.zero, self.events = True, now, []
        self.cursor = cursor_position()
        self.started = datetime.datetime.now()
        self.session = self.session or self.started
        self.number += 1
        self.gesture.reset()

    def cut(self):
        path = recording_path(self.session, self.number)
        self.saved.append(path)
        writer = threading.Thread(target=self.write, args=(
            path, self.started, self.cursor, self.events,
            f"recording {self.number} cut"))
        self.writers.append(writer)
        writer.start()
        self.recording, self.events = False, []

    def write(self, path, started, cursor, events, said):
        self.reports.append(save(path, started, cursor, events, said))

    def toggle(self, now):
        if not self.recording:
            if self.saved:
                self.finished = True
            else:
                self.begin(now)
            return
        gesture = {id(event) for event in self.gesture.events}
        self.events = [e for e in self.events if id(e) not in gesture]
        self.recording, self.finished, self.kept = False, True, True


class Film:
    def __init__(self, session, folder):
        self.session, self.folder = session, folder
        self.count = 0
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.roll, daemon=True)

    def roll(self):
        import mss
        from PIL import Image
        gap = 1.0 / SCREEN_FPS
        with mss.MSS() as sct, open(self.folder / SCREEN_INDEX, "a",
                                    encoding="utf-8", buffering=1) as index:
            where = sct.monitors[SCREEN_MONITOR]
            while not self.stop.is_set():
                due = time.perf_counter() + gap
                raw = sct.grab(where)
                taken = round(time.perf_counter() - self.session.zero, 6)
                at = datetime.datetime.now().isoformat(timespec="milliseconds")
                self.count += 1
                name = f"{self.count:05d}.png"
                Image.frombytes("RGB", raw.size, raw.bgra, "raw",
                                "BGRX").save(self.folder / name)
                index.write(json.dumps({"n": self.count, "t": taken,
                                        "at": at, "file": name}) + "\n")
                self.stop.wait(max(0.0, due - time.perf_counter()))


def hook(kind, handler, flag, struct):
    def proc(code, wparam, lparam):
        if code >= 0:
            info = ctypes.cast(lparam, ctypes.POINTER(struct)).contents
            if not info.flags & flag:
                handler(wparam, info)
        return user32.CallNextHookEx(None, code, wparam, lparam)
    callback = HOOKPROC(proc)
    handle = user32.SetWindowsHookExW(kind, callback,
                                      kernel32.GetModuleHandleW(None), 0)
    if not handle:
        raise OSError(ctypes.get_last_error(), "SetWindowsHookExW refused the hook")
    return handle, callback


def pump(done, tick):
    timer = user32.SetTimer(None, 0, PUMP_MS, None)
    msg = wintypes.MSG()
    try:
        while not done():
            if user32.GetMessageW(ctypes.byref(msg), None, 0, 0) <= 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
            tick()
    finally:
        user32.KillTimer(None, timer)


def recording_path(session, number):
    return OUT / f"{session:%Y-%m-%d_%H%M%S}_{number:02d}_actions.jsonl"


def save(path, started, cursor, events, said):
    OUT.mkdir(parents=True, exist_ok=True)
    width, height = screen()
    lines = [json.dumps({"kind": "start", "at": started.isoformat(
        timespec="seconds"), "screen": [width, height],
        "desktop": desktop(), "cursor": cursor})]
    lines += [json.dumps(event) for event in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    counts = {}
    for event in events:
        name = f"{event['kind']} {event['action']}"
        counts[name] = counts.get(name, 0) + 1
    length = events[-1]["t"] if events else 0.0
    report = [f"{said}: {len(events)} events over {length:.1f}s"]
    report += [f"  {name}: {count}" for name, count in sorted(counts.items())]
    return report + [f"saved {path}"]


def console_colours():
    if not sys.stdout.isatty():
        return False
    handle = kernel32.GetStdHandle(W["STD_OUTPUT_HANDLE"])
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    return bool(kernel32.SetConsoleMode(
        handle, mode.value | W["ENABLE_VIRTUAL_TERMINAL_PROCESSING"]))


class Signal:
    def __init__(self):
        self.colour = console_colours()
        self.said = None

    def paint(self, state, lines):
        if not self.colour:
            if lines[0] != self.said:
                self.said = lines[0]
                print(lines[0], flush=True)
            return
        look = BANNER[state]
        word = [letter for letter in look["word"] if letter in GLYPHS]
        cell = len(GLYPHS[word[0]][0])
        gap, margin = int(BANNER["letter_gap"]), int(BANNER["margin"])
        width, height = shutil.get_terminal_size()
        span = len(word) * cell + (len(word) - 1) * gap
        room = height - len(lines) - 3 * margin
        scale = max(1, min((width - 2 * margin) // span, room // GLYPH_ROWS))
        paper = f"{ESC}[0;{look['background']}m"
        ink = f"{ESC}[{BANNER['ink']}m"
        out = [f"{paper}{ESC}[2J{ESC}[H" + "\n" * margin]
        for row in range(GLYPH_ROWS):
            line = paper + " " * margin
            for at, letter in enumerate(word):
                if at:
                    line += paper + " " * (gap * scale)
                for pixel in GLYPHS[letter][row]:
                    line += (paper if pixel == " " else ink) + " " * scale
            out += [line + paper + "\n"] * scale
        text = f"{paper}{ESC}[{BANNER['text']}m"
        out.append("\n" * margin)
        out += [text + " " * margin + line + "\n" for line in lines]
        sys.stdout.write("".join(out))
        sys.stdout.flush()

    def close(self):
        if self.colour:
            sys.stdout.write(f"{ESC}[0m{ESC}[2J{ESC}[H")
            sys.stdout.flush()


def record(filming=False):
    user32.SetProcessDPIAware()
    session = Recording(segments=not filming)
    signal = Signal()
    hooks = [hook(W["WH_KEYBOARD_LL"], session.key, W["LLKHF_INJECTED"],
                  KBDLLHOOKSTRUCT),
             hook(W["WH_MOUSE_LL"], session.mouse, W["LLMHF_INJECTED"],
                  MSLLHOOKSTRUCT)]
    film = None
    if filming:
        session.toggle(time.perf_counter())
        film = Film(session, OUT / f"{session.session:%Y-%m-%d_%H%M%S}_"
                                   f"{SCREEN_FOLDER}")
        film.folder.mkdir(parents=True, exist_ok=True)
        film.worker.start()

    def status():
        if filming:
            return [f"recording the screen at {SCREEN_FPS:g} frames a second "
                    f"and every key and click since "
                    f"{session.started:%H:%M:%S}; press Ctrl {TAPS} times "
                    f"within {WINDOW:g}s to stop"]
        if session.recording:
            return [f"recording {session.number} since "
                    f"{session.started:%H:%M:%S}; Z saves it and pauses, Ctrl "
                    f"{TAPS} times within {WINDOW:g}s saves it and stops"]
        if not session.saved:
            return [f"paused: Z or Ctrl {TAPS} times within {WINDOW:g}s starts "
                    f"recording 1"]
        return ([f"paused: Z starts recording {session.number + 1}, Ctrl "
                 f"{TAPS} times within {WINDOW:g}s stops", ""]
                + (session.reports[-1] if session.reports else []))

    shown = {"state": None}

    def tick():
        state = (session.recording, session.number, len(session.reports))
        if state != shown["state"] and not session.finished:
            shown["state"] = state
            signal.paint("recording" if session.recording else "paused",
                         status())

    try:
        tick()
        pump(lambda: session.finished, tick)
    finally:
        for handle, _callback in hooks:
            user32.UnhookWindowsHookEx(handle)
        if film is not None:
            film.stop.set()
            film.worker.join()
        for writer in session.writers:
            writer.join()
        signal.close()
        if film is not None:
            print(f"saved {film.count} frame(s) in {film.folder}, indexed in "
                  f"{SCREEN_INDEX}", flush=True)
        for report in session.reports:
            print("\n".join(report), flush=True)
    if not session.finished:
        print("stopped before a recording was finished; nothing saved")
        return 1
    if session.kept:
        path = recording_path(session.session, session.number)
        session.saved.append(path)
        print("\n".join(save(path, session.started, session.cursor,
                             session.events, "stopped" if filming
                             else f"recording {session.number} stopped")),
              flush=True)
    if session.writers:
        print("saved this run, in order:")
        for path in session.saved:
            print(f"  {path.name}")
    return 0


def absolute(x, y):
    left, top, width, height = desktop()
    span = W["ABSOLUTE_SPAN"]
    return (max(0, min(span - 1, -(-(x - left) * span // width))),
            max(0, min(span - 1, -(-(y - top) * span // height))))


def send(item):
    if user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT)) != 1:
        raise OSError(ctypes.get_last_error(), "SendInput refused the event")


def key_input(event, up):
    flags = W["KEYEVENTF_SCANCODE"] if event["scan"] else 0
    if event["extended"]:
        flags |= W["KEYEVENTF_EXTENDEDKEY"]
    if up:
        flags |= W["KEYEVENTF_KEYUP"]
    return INPUT(type=W["INPUT_KEYBOARD"], u=INPUTUNION(ki=KEYBDINPUT(
        event["vk"], event["scan"], flags, 0, 0)))


def mouse_input(event, flags, data=0):
    dx, dy = absolute(event["x"], event["y"])
    flags |= (W["MOUSEEVENTF_MOVE"] | W["MOUSEEVENTF_ABSOLUTE"]
              | W["MOUSEEVENTF_VIRTUALDESK"])
    return INPUT(type=W["INPUT_MOUSE"], u=INPUTUNION(mi=MOUSEINPUT(
        dx, dy, ctypes.c_ulong(data).value, flags, 0, 0)))


def play_one(event, keys, buttons):
    if event["kind"] == "key":
        up = event["action"] == "up"
        send(key_input(event, up))
        if up:
            keys.pop(event["vk"], None)
        else:
            keys[event["vk"]] = event
        return
    action = event["action"]
    if action == "move":
        send(mouse_input(event, 0))
    elif action in WHEEL_FLAGS:
        send(mouse_input(event, WHEEL_FLAGS[action], event["delta"]))
    else:
        button = event["button"]
        send(mouse_input(event, BUTTON_FLAGS[(action, button)],
                         X_DATA.get(button, 0)))
        if action == "down":
            buttons[button] = event
        else:
            buttons.pop(button, None)


def wait_until(target, stop):
    while not stop.is_set():
        left = target - time.perf_counter()
        if left <= 0:
            return
        if left > SPIN_BELOW:
            stop.wait(left - SPIN_BELOW)
        else:
            time.sleep(0)


class Replay:
    def __init__(self, events):
        self.events = events
        self.gesture = Gesture()
        self.stop = threading.Event()
        self.worker = None
        self.finished = False
        self.played = 0
        self.said = None

    def key(self, message, info):
        down = message in KEY_DOWN
        if not down and message not in KEY_UP:
            return
        if self.gesture.key(info.vkCode, down, time.perf_counter(), None):
            if self.worker is None:
                self.worker = threading.Thread(target=self.play, daemon=True)
                self.worker.start()
                self.said = "started"
            else:
                self.stop.set()

    def play(self):
        keys, buttons = {}, {}
        winmm.timeBeginPeriod(TIMER_MS)
        zero = time.perf_counter()
        try:
            for event in self.events:
                wait_until(zero + event["t"], self.stop)
                if self.stop.is_set():
                    break
                play_one(event, keys, buttons)
                self.played += 1
        finally:
            for event in list(keys.values()):
                send(key_input(event, True))
            for button, event in list(buttons.items()):
                send(mouse_input(event, BUTTON_FLAGS[("up", button)],
                                 X_DATA.get(button, 0)))
            winmm.timeEndPeriod(TIMER_MS)
            self.finished = True


def replay(name=None):
    user32.SetProcessDPIAware()
    path = Path(name) if name else max(OUT.glob("*_actions.jsonl"),
                                       default=None)
    if path is None or not path.exists():
        print(f"no recording to replay in {OUT}")
        return 1
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    head, events = json.loads(lines[0]), [json.loads(l) for l in lines[1:]]
    here, layout = list(screen()), desktop()
    if head.get("screen") != here or head.get("desktop", layout) != layout:
        print(f"{path.name} was recorded on a {head.get('screen')} screen "
              f"(desktop {head.get('desktop', 'not noted')}) and this one is "
              f"{here} (desktop {layout}); the positions would not line up. "
              f"Nothing replayed.")
        return 1
    if not events:
        print(f"{path.name} holds no events; nothing to replay")
        return 1
    print(f"{path.name}: {len(events)} events over {events[-1]['t']:.1f}s, "
          f"recorded {head.get('at')}")
    print(f"switch to the game and press Ctrl {TAPS} times within {WINDOW:g}s "
          f"to start; the same again stops it early", flush=True)
    session = Replay(events)
    handle, _callback = hook(W["WH_KEYBOARD_LL"], session.key,
                             W["LLKHF_INJECTED"], KBDLLHOOKSTRUCT)

    def tick():
        if session.said == "started":
            print(f"replaying from {datetime.datetime.now():%H:%M:%S}",
                  flush=True)
            session.said = None

    try:
        pump(lambda: session.finished, tick)
    finally:
        session.stop.set()
        if session.worker is not None:
            session.worker.join()
        user32.UnhookWindowsHookEx(handle)
    whole = session.played == len(events)
    print(f"{'replayed' if whole else 'stopped after'} {session.played} of "
          f"{len(events)} events; nothing is left held down")
    return 0


def submit():
    import platform
    import submit_bug
    files = sorted(OUT.glob("*_actions.jsonl"))
    if not files:
        print(f"no recordings in {OUT}; nothing to submit")
        return 1
    mine = f"{SUBMIT_FOLDER}/{submit_bug.machine_id()}"

    def fill(work):
        base = work / mine
        base.mkdir(parents=True, exist_ok=True)
        new = [f for f in files if not (base / f.name).exists()
               or (base / f.name).read_bytes() != f.read_bytes()]
        for f in new:
            shutil.copy2(str(f), str(base / f.name))
        return new, (f"recordings: {len(new)} from {platform.node()}, "
                     f"{new[0].name} to {new[-1].name}" if new else "")

    print(f"submitting the recordings in {OUT} from {platform.node()} "
          f"({submit_bug.machine_id()})")
    try:
        commit, new = submit_bug.submit(mine, fill)
    except submit_bug.Fail as exc:
        print(f"  {exc}")
        return 1
    if commit is None:
        print(f"  all {len(files)} are already in {mine}; nothing pushed")
        return 0
    print(f"  pushed {commit} to {submit_bug.K['remote']}/"
          f"{submit_bug.K['branch']}: {len(new)} new in {mine}")
    for f in new:
        print(f"    {f.name}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["replay"]:
        sys.exit(replay(args[1] if len(args) > 1 else None))
    if args[:1] == ["submit"]:
        sys.exit(submit())
    sys.exit(record(filming=args[:1] == ["screen"]))
