import collections
import ctypes
import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import recorder_settings
import online_clock
import record_action as ra

W = ra.W
SUFFIX = ra.R["all_suffix"]
POLL = float(ra.R["all_poll_ms"]) / 1000.0
FLUSH = float(ra.R["all_flush_seconds"])
SETTLE = float(ra.R["all_focus_settle"])
SHOT_SIZE = tuple(ra.R["all_shot_compare"])
VK_PACKET = W["VK_PACKET"]
TITLE = json.loads((ra.SRC / "calibration.json").read_text(
    encoding="utf-8"))["game"]["title_hint"]
EXPLAINED = 0.1
POLL_GAP = float(ra.R["all_stop_poll"])
OWN_GRACE = 0.1
TURN = set(ra.R["all_turn_buttons"])
ZOOM = ra.R.get("all_zoom_out") or {"notches": 0, "delta": 120, "gap_ms": 30,
                                     "settle": 0.5}

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR)]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT),
                ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD),
                ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWMOUSE(ctypes.Structure):
    _fields_ = [("usFlags", wintypes.USHORT), ("ulButtons", wintypes.ULONG),
                ("ulRawButtons", wintypes.ULONG), ("lLastX", wintypes.LONG),
                ("lLastY", wintypes.LONG), ("ulExtraInformation", wintypes.ULONG)]


class RAWINPUT(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


ra.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
ra.user32.DefWindowProcW.restype = LRESULT
ra.user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
    wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
ra.user32.CreateWindowExW.restype = wintypes.HWND
ra.user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                      wintypes.LPVOID, ctypes.POINTER(wintypes.UINT),
                                      wintypes.UINT]
ra.user32.GetRawInputData.restype = wintypes.UINT


class RawMouse:

    def __init__(self, handler):
        self.handler = handler
        self.proc = WNDPROC(self.message)
        instance = ra.kernel32.GetModuleHandleW(None)
        name = f"record_all_raw_{os.getpid()}_{id(self)}"
        self.cls = WNDCLASSW(lpfnWndProc=self.proc, hInstance=instance,
                             lpszClassName=name)
        if not ra.user32.RegisterClassW(ctypes.byref(self.cls)):
            raise OSError(ctypes.get_last_error(), "RegisterClassW refused")
        self.hwnd = ra.user32.CreateWindowExW(
            0, name, None, 0, 0, 0, 0, 0, wintypes.HWND(W["HWND_MESSAGE"]),
            None, instance, None)
        if not self.hwnd:
            raise OSError(ctypes.get_last_error(), "CreateWindowExW refused")
        self.register(W["RIDEV_INPUTSINK"], self.hwnd)

    def register(self, flags, hwnd):
        device = RAWINPUTDEVICE(W["HID_USAGE_PAGE_GENERIC"],
                                W["HID_USAGE_GENERIC_MOUSE"], flags, hwnd)
        if not ra.user32.RegisterRawInputDevices(
                ctypes.byref(device), 1, ctypes.sizeof(device)):
            raise OSError(ctypes.get_last_error(),
                          "RegisterRawInputDevices refused")

    def message(self, hwnd, msg, wparam, lparam):
        if msg == W["WM_INPUT"]:
            try:
                data = RAWINPUT()
                size = wintypes.UINT(ctypes.sizeof(data))
                got = ra.user32.GetRawInputData(
                    lparam, W["RID_INPUT"], ctypes.byref(data),
                    ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
                if (got not in (0, 0xFFFFFFFF)
                        and data.header.dwType == W["RIM_TYPEMOUSE"]):
                    self.handler(data.mouse.usFlags, data.mouse.lLastX,
                                 data.mouse.lLastY, bool(data.header.hDevice))
            except Exception:
                pass
        return ra.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def close(self):
        try:
            self.register(W["RIDEV_REMOVE"], None)
        finally:
            ra.user32.DestroyWindow(self.hwnd)


def listen_raw(session):
    try:
        return RawMouse(session.raw)
    except OSError as exc:
        print(f"  raw mouse counts are not available ({exc}); camera turns "
              f"will replay from cursor positions only", flush=True)
        return None


def online_now():
    now = online_clock.utc_now()
    if now is None:
        raise RuntimeError("no online time: no time server answered and none "
                           "was measured before")
    return now.astimezone().replace(tzinfo=None)


def recording_path(started):
    return ra.OUT / f"{started:%Y-%m-%d_%H%M%S}{SUFFIX}"


def shot_path(path):
    return path.with_name(path.name[:-len(SUFFIX)] + "_start.png")


def find_game():
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _lparam):
        if not ra.user32.IsWindowVisible(hwnd):
            return True
        length = ra.user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        ra.user32.GetWindowTextW(hwnd, buf, length + 1)
        if TITLE.casefold() in buf.value.casefold():
            found.append(hwnd)
            return False
        return True

    ra.user32.EnumWindows(proto(each), 0)
    return found[0] if found else None


def game_rect():
    hwnd = find_game()
    if hwnd is None:
        return None
    rect = wintypes.RECT()
    ra.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return [rect.left, rect.top, rect.right, rect.bottom]


def focus_game():
    hwnd = find_game()
    if hwnd is None:
        return False
    if ra.user32.GetForegroundWindow() == hwnd:
        return True
    if ra.user32.IsIconic(hwnd):
        ra.user32.ShowWindow(hwnd, W["SW_RESTORE"])
    current = ra.kernel32.GetCurrentThreadId()
    threads = {ra.user32.GetWindowThreadProcessId(hwnd, None),
               ra.user32.GetWindowThreadProcessId(
                   ra.user32.GetForegroundWindow(), None)} - {current, 0}
    for thread in threads:
        ra.user32.AttachThreadInput(current, thread, True)
    try:
        ra.user32.BringWindowToTop(hwnd)
        ra.user32.SetForegroundWindow(hwnd)
    finally:
        for thread in threads:
            ra.user32.AttachThreadInput(current, thread, False)
    time.sleep(SETTLE)
    return ra.user32.GetForegroundWindow() == hwnd


def grab(rect):
    import mss
    from PIL import Image
    x, y, width, height = ra.desktop()
    left, top = max(rect[0], x), max(rect[1], y)
    right, bottom = min(rect[2], x + width), min(rect[3], y + height)
    with mss.MSS() as sct:
        raw = sct.grab({"left": left, "top": top, "width": right - left,
                        "height": bottom - top})
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def difference(a, b):
    small = [image.convert("L").resize(SHOT_SIZE) for image in (a, b)]
    pairs = zip(small[0].getdata(), small[1].getdata())
    return sum(abs(p - q) for p, q in pairs) / (255.0 * SHOT_SIZE[0] * SHOT_SIZE[1])


class Everything:
    def __init__(self, control=True, command=None, armed=False, zoom=0):
        self.control = control
        self.zoom = zoom
        self.zooming = False
        self.zoomed = 0
        self.command = command
        self.gesture = ra.Gesture()
        self.lock = threading.Lock()
        self.held = set()
        self.dropped = set()
        self.ctrl_taps = []
        self.hooked = collections.deque(maxlen=256)
        self.finished = False
        self.opened = False
        self.opener = None
        self.window = None
        self.shot = None
        self.written = 0
        self.recording = False
        if armed:
            self.started = self.path = None
            self.events = []
        else:
            self.start(time.perf_counter())

    def start(self, now):
        self.zero = now
        self.started = online_now()
        self.cursor = ra.cursor_position()
        self.last = tuple(self.cursor)
        self.path = recording_path(self.started)
        self.events = []
        self.recording = True

    def zoom_then_start(self):
        try:
            zoom_out(self.zoom)
            with self.lock:
                self.start(time.perf_counter())
                self.zoomed = self.zoom
        finally:
            self.zooming = False

    def add(self, now, event):
        stamped = {"t": round(now - self.zero, 6), **event}
        self.events.append(stamped)
        return stamped

    def key(self, message, info):
        now = time.perf_counter()
        down = message in ra.KEY_DOWN
        if not down and message not in ra.KEY_UP:
            return
        injected = bool(info.flags & W["LLKHF_INJECTED"])
        vk = info.vkCode
        action = "down" if down else "up"
        event = None
        if vk == VK_PACKET:
            return
        extended = bool(info.flags & W["LLKHF_EXTENDED"])
        with self.lock:
            repeat = down and vk in self.held
            if down:
                self.held.add(vk)
            else:
                self.held.discard(vk)
            if self.recording:
                event = self.add(now, {
                    "kind": "key", "action": action, "vk": vk,
                    "scan": info.scanCode, "extended": extended,
                    "name": ra.key_name(info.scanCode, extended),
                    "repeat": repeat, "injected": injected})
        if injected or repeat or not self.control:
            return
        if vk in ra.CTRL and event is not None:
            self.ctrl_taps = (self.ctrl_taps + [event])[-2 * ra.TAPS:]
        if self.gesture.key(vk, down, now, event):
            if self.recording:
                self.dropped = {id(e) for e in self.ctrl_taps}
                self.finished = True
            elif self.zooming:
                self.gesture.reset()
            elif self.zoom:
                self.gesture.reset()
                self.zooming = True
                threading.Thread(target=self.zoom_then_start, daemon=True).start()
            else:
                self.gesture.reset()
                with self.lock:
                    self.start(now)

    def mouse(self, message, info):
        now = time.perf_counter()
        injected = bool(info.flags & W["LLMHF_INJECTED"])
        x, y = info.pt.x, info.pt.y
        high = ctypes.c_short(info.mouseData >> W["HIGH_WORD_SHIFT"]).value
        if message in ra.BUTTON_MESSAGES:
            action, button = ra.BUTTON_MESSAGES[message]
            if button == "x":
                button = "x1" if high == W["XBUTTON1"] else "x2"
            if not injected:
                self.gesture.reset()
            event = {"kind": "mouse", "action": action, "button": button,
                     "x": x, "y": y}
        elif message in ra.WHEELS:
            event = {"kind": "mouse", "action": ra.WHEELS[message],
                     "delta": high, "x": x, "y": y}
        elif message == W["WM_MOUSEMOVE"]:
            event = {"kind": "mouse", "action": "move", "x": x, "y": y}
        else:
            return
        if not self.recording:
            return
        with self.lock:
            self.add(now, {**event, "injected": injected})
            self.hooked.append(((x, y), now))

    def raw(self, flags, dx, dy, physical):
        if (not self.recording or not physical or not (dx or dy)
                or flags & W["MOUSE_MOVE_ABSOLUTE"]):
            return
        now = time.perf_counter()
        with self.lock:
            self.add(now, {"kind": "mouse", "action": "raw", "dx": dx, "dy": dy})

    def poll(self, where):
        if not self.recording:
            return False
        with self.lock:
            if where == self.last:
                return False
            self.last = where
            now = time.perf_counter()
            for at, (pos, when) in enumerate(self.hooked):
                if pos == where and now - when <= EXPLAINED:
                    for _ in range(at + 1):
                        self.hooked.popleft()
                    return False
            self.add(now, {"kind": "mouse", "action": "move", "x": where[0],
                           "y": where[1], "source": "poll"})
            return True

    def header(self):
        width, height = ra.screen()
        return {"kind": "start", "at": self.started.isoformat(timespec="seconds"),
                "screen": [width, height], "desktop": ra.desktop(),
                "cursor": self.cursor, "command": self.command,
                "window": self.window, "start_shot": self.shot,
                "zoom_out": self.zoomed, "recorder": "record_all"}

    def open(self, window=None, picture=None):
        self.window = window
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stem, again = self.path.name[:-len(SUFFIX)], 1
        while self.path.exists():
            again += 1
            self.path = self.path.with_name(f"{stem}_{again}{SUFFIX}")
        if picture is not None:
            picture.save(shot_path(self.path))
            self.shot = shot_path(self.path).name
        self.path.write_text(json.dumps(self.header()) + "\n", encoding="utf-8")
        self.opened = True

    def flush(self):
        if not self.opened:
            return
        with self.lock:
            batch = self.events[self.written:]
            self.written = len(self.events)
            lines = [json.dumps(event) for event in batch]
        if lines:
            with open(self.path, "a", encoding="utf-8") as out:
                out.write("\n".join(lines) + "\n")

    def finish(self):
        if not self.opened:
            return ["stopped before recording began; nothing saved"]
        with self.lock:
            events = sorted((e for e in self.events if id(e) not in self.dropped),
                            key=lambda e: e["t"])
            lines = [json.dumps(self.header())]
            lines += [json.dumps(event) for event in events]
        spare = self.path.with_name(self.path.name + ".part")
        spare.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(spare, self.path)
        return report(self.path, events)


def report(path, events):
    counts = collections.Counter(f"{e['kind']} {e['action']}" for e in events)
    bot = sum(1 for e in events if e.get("injected") is True)
    you = sum(1 for e in events if e.get("injected") is False)
    polled = sum(1 for e in events if e.get("source") == "poll")
    length = events[-1]["t"] if events else 0.0
    lines = [f"{len(events)} events over {length:.1f}s: {bot} sent by software, "
             f"{you} by hand, {polled} cursor moves seen by polling"]
    lines += [f"  {name}: {count}" for name, count in sorted(counts.items())]
    return lines + [f"saved {path}"]


def _every(gap, stop, work):
    def loop():
        while not stop.is_set():
            work()
            stop.wait(gap)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread


def open_session(session):
    window = game_rect()
    picture = None
    if window is not None:
        try:
            picture = grab(window)
        except Exception as exc:
            print(f"  no start picture: {exc}", flush=True)
    session.open(window, picture)


def record(command=None):
    ra.user32.SetProcessDPIAware()
    session = Everything(control=command is None, command=command,
                         armed=command is None,
                         zoom=int(ZOOM["notches"]) if command is None else 0)
    if command:
        open_session(session)
    hooks = [ra.hook(W["WH_KEYBOARD_LL"], session.key, 0, ra.KBDLLHOOKSTRUCT),
             ra.hook(W["WH_MOUSE_LL"], session.mouse, 0, ra.MSLLHOOKSTRUCT)]
    raw = listen_raw(session)
    stop = threading.Event()
    ra.winmm.timeBeginPeriod(ra.TIMER_MS)
    workers = [_every(POLL, stop, lambda: session.poll(tuple(ra.cursor_position()))),
               _every(FLUSH, stop, session.flush)]
    child, code, previous = None, 0, None
    signal_box = ra.Signal() if command is None else None
    shown = {"state": None}

    def tick():
        if session.recording and session.opener is None and not session.opened:
            session.opener = threading.Thread(target=open_session,
                                              args=(session,), daemon=True)
            session.opener.start()
        state = session.recording
        if signal_box is not None and state != shown["state"] and not session.finished:
            shown["state"] = state
            if state:
                signal_box.paint("recording", [
                    f"recording every key, click and cursor move since "
                    f"{session.started:%H:%M:%S}; press Ctrl {ra.TAPS} times "
                    f"within {ra.WINDOW:g}s to stop and save"])
            else:
                signal_box.paint("paused", [
                    f"ready: click into the game, set it up the way the run "
                    f"should start, then press Ctrl {ra.TAPS} times within "
                    f"{ra.WINDOW:g}s to start recording"])

    try:
        if command:
            print(f"recording everything since {session.started:%H:%M:%S} "
                  f"while {' '.join(command)} runs; it stops when that ends",
                  flush=True)
            previous = signal.signal(signal.SIGINT, lambda *a: None)
            try:
                child = subprocess.Popen(command)
            except OSError as exc:
                print(f"could not start {' '.join(command)}: {exc}")
                code = 1
            done = (lambda: child.poll() is not None) if child else (lambda: True)
        else:
            done = lambda: session.finished
        tick()
        try:
            ra.pump(done, tick)
        except KeyboardInterrupt:
            code = 1
        if child is not None:
            code = child.wait()
    finally:
        for handle, _callback in hooks:
            if handle:
                ra.user32.UnhookWindowsHookEx(handle)
        if raw is not None:
            raw.close()
        stop.set()
        for worker in workers:
            worker.join()
        ra.winmm.timeEndPeriod(ra.TIMER_MS)
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
        if signal_box is not None:
            signal_box.close()
        if session.opener is not None:
            session.opener.join()
        session.flush()
        print("\n".join(session.finish()), flush=True)
    if child is not None:
        print(f"{' '.join(command)} exited with {code}")
    return code


def cursor_now():
    point = ra.wintypes.POINT()
    ra.user32.GetCursorPos(ra.ctypes.byref(point))
    return point.x, point.y


def set_cursor(x, y):
    ra.user32.SetCursorPos(x, y)


def place(x, y):
    if cursor_now() == (x, y):
        return
    ra.send(ra.mouse_input({"x": x, "y": y}, 0))
    if cursor_now() != (x, y):
        set_cursor(x, y)


def zoom_out(notches, sleep=None):
    sleep = sleep or time.sleep
    for _ in range(int(notches)):
        ra.send(wheel_input("wheel", int(ZOOM["delta"])))
        sleep(float(ZOOM["gap_ms"]) / 1000.0)
    sleep(float(ZOOM["settle"]))


def wheel_input(action, delta):
    return ra.INPUT(type=W["INPUT_MOUSE"], u=ra.INPUTUNION(mi=ra.MOUSEINPUT(
        0, 0, ctypes.c_ulong(delta).value, ra.WHEEL_FLAGS[action], 0, 0)))


def relative_input(dx, dy):
    return ra.INPUT(type=W["INPUT_MOUSE"], u=ra.INPUTUNION(mi=ra.MOUSEINPUT(
        dx, dy, 0, W["MOUSEEVENTF_MOVE"], 0, 0)))


def button_input(button, action):
    return ra.INPUT(type=W["INPUT_MOUSE"], u=ra.INPUTUNION(mi=ra.MOUSEINPUT(
        0, 0, ctypes.c_ulong(ra.X_DATA.get(button, 0)).value,
        ra.BUTTON_FLAGS[(action, button)], 0, 0)))


def play_event(event, keys, buttons, turning=None):
    if "secret" in event or event.get("vk") == VK_PACKET:
        return False
    action = event["action"]
    if action == "raw":
        if turning:
            ra.send(relative_input(event["dx"], event["dy"]))
            return True
        return False
    if event["kind"] == "mouse" and turning is not None:
        button = event.get("button")
        if action == "move" and turning and event.get("injected") is False:
            return False
        if button in TURN and action == "down" and not event.get("injected"):
            if not turning:
                place(event["x"], event["y"])
            ra.send(button_input(button, "down"))
            buttons[button] = event
            turning.add(button)
            return True
        if button in TURN and action == "up" and button in turning:
            turning.discard(button)
            ra.send(button_input(button, "up"))
            buttons.pop(button, None)
            if not turning:
                place(event["x"], event["y"])
            return True
    if event["kind"] == "mouse":
        if action == "move" and turning:
            if cursor_now() != (event["x"], event["y"]):
                set_cursor(event["x"], event["y"])
        elif action == "move" or not turning:
            place(event["x"], event["y"])
        if action in ra.WHEEL_FLAGS:
            ra.send(wheel_input(action, event["delta"]))
        elif action in ("down", "up"):
            button = event["button"]
            ra.send(button_input(button, action))
            if action == "down":
                buttons[button] = event
            else:
                buttons.pop(button, None)
        return True
    ra.play_one(event, keys, buttons)
    return True


class Replay(ra.Replay):
    def __init__(self, events, cursor, head=None, base=None, on_ctrl=None):
        super().__init__(events)
        self.cursor = cursor
        self.head = head or {}
        self.base = base
        self.late = []
        self.refused = None
        self.turning = set() if any(e["action"] == "raw" for e in events) else None
        self.own_ctrl = False
        self.on_ctrl = on_ctrl

    def holding_ctrl(self, held):
        if held != self.own_ctrl:
            self.own_ctrl = held
            if self.on_ctrl is not None:
                self.on_ctrl(held)

    def ready(self):
        if not self.head.get("window"):
            return True
        if not focus_game():
            self.refused = "could not bring the game to the front; nothing replayed"
            return False
        if self.base is not None and self.head.get("start_shot"):
            picture = self.base / self.head["start_shot"]
            if picture.exists():
                try:
                    from PIL import Image
                    gap = difference(Image.open(picture), grab(self.head["window"]))
                    print(f"  the game now differs from the recording's start "
                          f"by {gap:.0%}; the closer to 0%, the closer the "
                          f"replay follows", flush=True)
                except Exception as exc:
                    print(f"  could not compare the start picture: {exc}",
                          flush=True)
        return True

    def play(self):
        keys, buttons = {}, {}
        ra.winmm.timeBeginPeriod(ra.TIMER_MS)
        try:
            if not self.ready():
                return
            if self.cursor:
                place(self.cursor[0], self.cursor[1])
            if self.head.get("zoom_out"):
                zoom_out(self.head["zoom_out"])
            zero = time.perf_counter()
            for event in self.events:
                due = zero + event["t"]
                ra.wait_until(due, self.stop)
                if self.stop.is_set():
                    break
                ctrl = event["kind"] == "key" and event.get("vk") in ra.CTRL
                if ctrl and event["action"] == "down":
                    self.holding_ctrl(True)
                play_event(event, keys, buttons, self.turning)
                self.late.append(time.perf_counter() - due)
                self.played += 1
                if ctrl and not any(vk in ra.CTRL for vk in keys):
                    self.holding_ctrl(False)
        finally:
            for event in list(keys.values()):
                ra.send(ra.key_input(event, True))
            for button, event in list(buttons.items()):
                ra.send(button_input(button, "up"))
            self.holding_ctrl(False)
            ra.winmm.timeEndPeriod(ra.TIMER_MS)
            self.finished = True

    def timing(self):
        if not self.late:
            return "no events were sent"
        ms = sorted(late * 1000 for late in self.late)
        return (f"each event was sent on average {statistics.mean(ms):.2f}ms "
                f"after its recorded moment (median {statistics.median(ms):.2f}ms, "
                f"worst {ms[-1]:.1f}ms)")


class CtrlTaps:

    def __init__(self, own=lambda: False, state=None):
        self.own = own
        self.state = state or (lambda: bool(
            ra.user32.GetAsyncKeyState(W["VK_CONTROL"]) & W["KEY_STATE_DOWN"]))
        self.seen = []
        self.held = False
        self.quiet_until = 0.0

    def step(self, now):
        if self.own():
            self.seen, self.held = [], True
            self.quiet_until = now + OWN_GRACE
            return False
        if now < self.quiet_until:
            self.held = self.state()
            return False
        down = self.state()
        tapped = down and not self.held
        self.held = down
        if not tapped:
            return False
        self.seen = [at for at in self.seen if now - at <= ra.WINDOW] + [now]
        if len(self.seen) >= ra.TAPS:
            self.seen = []
            return True
        return False


def wait_for_taps(taps, gap, until=lambda: False):
    while not until():
        if taps.step(time.perf_counter()):
            return True
        time.sleep(gap)
    return False


def read_recording(path):
    lines = [line for line in path.read_text(encoding="utf-8").splitlines()
             if line.strip()]
    if not lines:
        return None, None, f"{path.name} is empty; nothing to replay"
    try:
        head, events = json.loads(lines[0]), [json.loads(l) for l in lines[1:]]
    except ValueError:
        good = []
        for line in lines:
            try:
                good.append(json.loads(line))
            except ValueError:
                break
        head, events = good[0] if good else {}, good[1:]
    if head.get("kind") != "start":
        return None, None, f"{path.name} has no start line; nothing replayed"
    events.sort(key=lambda e: e["t"])
    return head, events, None


def prepare(path):
    head, events, refused = read_recording(path)
    if refused:
        return None, None, refused
    here, layout = list(ra.screen()), ra.desktop()
    if head.get("screen") != here or head.get("desktop", layout) != layout:
        return None, None, (
            f"{path.name} was recorded on a {head.get('screen')} screen "
            f"(desktop {head.get('desktop', 'not noted')}) and this one is "
            f"{here} (desktop {layout}); the positions would not line up. "
            f"Nothing replayed.")
    if not events:
        return None, None, f"{path.name} holds no events; nothing to replay"
    if head.get("window"):
        now = game_rect()
        if now is None:
            return None, None, (f"{path.name} was recorded in the game and no "
                                f"{TITLE} window is open; nothing replayed.")
        if now != head["window"]:
            return None, None, (
                f"the game window was at {head['window']} when recorded and is "
                f"at {now} now; every click would land off by the difference. "
                f"Put it back and replay again. Nothing replayed.")
    return head, events, None


def replay(name=None):
    ra.user32.SetProcessDPIAware()
    path = Path(name) if name else max(ra.OUT.glob(f"*{SUFFIX}"), default=None)
    if path is None or not path.exists():
        print(f"no recording to replay in {ra.OUT}")
        return 1
    head, events, refused = prepare(path)
    if refused:
        print(refused)
        return 1
    typed = sum(1 for e in events if ("secret" in e or e.get("vk") == VK_PACKET)
                and e["action"] == "down")
    print(f"{path.name}: {len(events)} events over {events[-1]['t']:.1f}s, "
          f"recorded {head.get('at')}"
          + (f" while running {' '.join(head['command'])}"
             if head.get("command") else ""))
    if typed:
        print(f"  {typed} character(s) typed by software in this older "
              f"recording are skipped; only key presses and the mouse replay")
    if head.get("start_shot"):
        print(f"  set the game up like {path.parent / head['start_shot']} "
              f"(same spot, facing, camera and zoom) before starting")
    print(f"press Ctrl {ra.TAPS} times within {ra.WINDOW:g}s to start; the "
          f"game is brought to the front and the cursor goes to "
          f"{head.get('cursor')} first. The same again stops it early. Leave "
          f"the mouse and keyboard alone while it plays.", flush=True)
    session = Replay(events, head.get("cursor"), head, path.parent)
    taps = CtrlTaps(own=lambda: session.own_ctrl)
    try:
        wait_for_taps(taps, POLL_GAP)
    except KeyboardInterrupt:
        print("nothing replayed")
        return 1
    now = online_clock.utc_now()
    print("replaying" + (f" from {now.astimezone():%H:%M:%S}" if now else ""),
          flush=True)
    session.worker = threading.Thread(target=session.play, daemon=True)
    session.worker.start()
    try:
        if wait_for_taps(taps, POLL_GAP, until=lambda: session.finished):
            session.stop.set()
    except KeyboardInterrupt:
        session.stop.set()
    finally:
        session.worker.join()
    if session.refused:
        print(session.refused)
        return 1
    whole = session.played == len(events)
    print(f"{'replayed' if whole else 'stopped after'} {session.played} of "
          f"{len(events)} events; nothing is left held down")
    print(f"  {session.timing()}")
    return 0


def _signature(event):
    if event["kind"] == "key":
        return ("key", event.get("vk"), event["action"])
    if event["action"] in ("down", "up"):
        return ("button", event["button"], event["action"])
    return ("wheel", event["action"], event.get("delta"))


def _turn_releases(events):
    held, releases = set(), set()
    raw = any(e["action"] == "raw" for e in events)
    for index, event in enumerate(events):
        button = event.get("button")
        if not raw or button not in TURN or event.get("injected"):
            continue
        if event["action"] == "down":
            held.add(button)
        elif event["action"] == "up" and button in held:
            held.discard(button)
            releases.add(index)
    return releases


def _spread(values):
    ordered = sorted(values)
    return (statistics.median(ordered), ordered[int(0.95 * (len(ordered) - 1))],
            ordered[-1])


def compare(original, capture):
    head_a, a, refused = read_recording(original)
    if refused:
        return {"refused": refused}
    head_b, b, refused = read_recording(capture)
    if refused:
        return {"refused": refused}
    injected = [e for e in b if e.get("injected")]
    sent = injected or b
    by_hand = sum(1 for e in b if e.get("injected") is False) if injected else 0
    wanted = [e for e in a if e["kind"] in ("key", "mouse")
              and e["action"] not in ("move", "raw")]
    got = [e for e in sent if e["kind"] in ("key", "mouse")
           and e["action"] not in ("move", "raw")]
    releases = _turn_releases(a)
    release_ids = {id(a[i]) for i in releases}
    import difflib
    matcher = difflib.SequenceMatcher(None, [_signature(e) for e in wanted],
                                      [_signature(e) for e in got], autojunk=False)
    pairs = [(wanted[block.a + k], got[block.b + k])
             for block in matcher.get_matching_blocks() for k in range(block.size)]
    out = {"wanted": len(wanted), "got": len(got), "matched": len(pairs),
           "missing": len(wanted) - len(pairs), "extra": len(got) - len(pairs),
           "by_hand": by_hand, "first_missing": None, "clicks_off": [],
           "turn_ends_off": []}
    matched_ids = {id(x) for x, _y in pairs}
    out["first_missing"] = next((x for x in wanted if id(x) not in matched_ids), None)
    if pairs:
        shift = [y["t"] - x["t"] for x, y in pairs]
        offset = statistics.median(shift)
        jitter = [abs(s - offset) * 1000 for s in shift]
        out["offset"] = offset
        out["jitter_ms"] = _spread(jitter)
        tenth = max(1, len(shift) // 10)
        out["drift_ms"] = (statistics.median(shift[-tenth:])
                           - statistics.median(shift[:tenth])) * 1000
        for x, y in pairs:
            if x["kind"] != "mouse" or "x" not in x or "x" not in y:
                continue
            off = max(abs(x["x"] - y["x"]), abs(x["y"] - y["y"]))
            if off > ra.R["all_compare_click_px"]:
                (out["turn_ends_off"] if id(x) in release_ids
                 else out["clicks_off"]).append((x["t"], off))
    out["moves"] = (sum(1 for e in a if e["action"] == "move"),
                    sum(1 for e in sent if e["action"] == "move"))
    return out


def compare_report(original, capture):
    found = compare(original, capture)
    if "refused" in found:
        return [found["refused"]]
    lines = [f"{original.name} against {capture.name}: {found['matched']} of "
             f"{found['wanted']} keys, clicks and wheel turns arrived in order"]
    if found["missing"] or found["extra"]:
        first = found["first_missing"]
        lines.append(f"  {found['missing']} missing and {found['extra']} extra"
                     + (f"; the first missing is {_signature(first)} at "
                        f"{first['t']:.3f}s" if first else ""))
    if "offset" in found:
        median, p95, worst = found["jitter_ms"]
        lines.append(f"  timing: each arrived within {median:.2f}ms of its "
                     f"place (95% within {p95:.2f}ms, worst {worst:.1f}ms); "
                     f"the end drifted {found['drift_ms']:+.1f}ms against the start")
    lines.append(f"  clicks off by more than {ra.R['all_compare_click_px']}px: "
                 f"{len(found['clicks_off'])}"
                 + (f", the first at {found['clicks_off'][0][0]:.3f}s by "
                    f"{found['clicks_off'][0][1]}px" if found["clicks_off"] else ""))
    if found["turn_ends_off"]:
        lines.append(f"  camera turns whose cursor ended elsewhere before being "
                     f"put back: {len(found['turn_ends_off'])} (expected: the turn "
                     f"itself follows the raw counts)")
    lines.append(f"  cursor moves: {found['moves'][0]} recorded, "
                 f"{found['moves'][1]} replayed")
    if found["by_hand"]:
        lines.append(f"  {found['by_hand']} input(s) were made by hand during the "
                     f"replay and are left out")
    return lines


USAGE = ("use: record_all.py            record by hand: Ctrl "
         f"{ra.TAPS} times starts, the same again stops\n"
         "     record_all.py run CMD... record while CMD runs\n"
         "     record_all.py replay [FILE]\n"
         "     record_all.py compare RECORDING CAPTURE")


def main(args):
    if args[:1] == ["compare"]:
        if len(args) != 3:
            return USAGE
        paths = [Path(name) if Path(name).exists() else ra.OUT / name
                 for name in args[1:]]
        missing = [str(p) for p in paths if not p.exists()]
        if missing:
            print(f"no recording {', '.join(missing)}")
            return 1
        print("\n".join(compare_report(*paths)))
        return 0
    if args[:1] == ["replay"]:
        if len(args) > 2:
            return USAGE
        return replay(args[1] if len(args) > 1 else None)
    if args[:1] == ["run"]:
        if len(args) < 2:
            return USAGE
        return record(args[1:])
    if args:
        return USAGE
    return record()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
