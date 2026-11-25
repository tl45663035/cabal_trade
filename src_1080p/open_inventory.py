import ctypes
import time

import calibration

_CAL = calibration.load_shared()
_IN = _CAL["input"]
_T = _CAL["timing"]

INPUT_MOUSE = _IN["INPUT_MOUSE"]
INPUT_KEYBOARD = _IN["INPUT_KEYBOARD"]
KEYEVENTF_KEYUP = _IN["KEYEVENTF_KEYUP"]
KEYEVENTF_SCANCODE = _IN["KEYEVENTF_SCANCODE"]
MAPVK_VK_TO_VSC = _IN["MAPVK_VK_TO_VSC"]

VK_I = _IN["VK_I"]
SW_RESTORE = _IN["SW_RESTORE"]
VK_MENU = _IN["VK_MENU"]
VK_ESCAPE = _IN["VK_ESCAPE"]

KEY_HOLD = _T["key_hold"]
FOCUS_SETTLE = _T["focus_settle"]

GAME_TITLE = _CAL["game"]["title_hint"]

_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


assert ctypes.sizeof(_Input) == _IN["INPUT_STRUCT_SIZE"], (
    f"sizeof(INPUT) is {ctypes.sizeof(_Input)}, must be "
    f"{_IN['INPUT_STRUCT_SIZE']} on 64-bit Windows; SendInput refuses "
    f"anything else")


def _event(vk: int, up: bool) -> _Input:
    scan = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  u=_InputUnion(ki=_KeyInput(vk, scan, flags, 0, None)))


def press(vk: int, hold: float = None) -> None:
    calibration.halt_if_stopped()
    sent = _user32.SendInput(
        1, ctypes.byref(_event(vk, up=False)), ctypes.sizeof(_Input))
    try:
        if sent != 1:
            err = ctypes.get_last_error()
            raise OSError(err, f"SendInput sent {sent} of 1 events "
                               f"(GetLastError {err}). Nothing was pressed.")
        time.sleep(KEY_HOLD if hold is None else hold)
    finally:
        _user32.SendInput(
            1, ctypes.byref(_event(vk, up=True)), ctypes.sizeof(_Input))


def find_game_window() -> "int | None":
    found: list[int] = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        if GAME_TITLE.casefold() in buf.value.casefold():
            found.append(hwnd)
            return False
        return True

    _user32.EnumWindows(proto(callback), None)
    return found[0] if found else None


def focus_game(settle: float = FOCUS_SETTLE) -> bool:
    hwnd = find_game_window()
    if hwnd is None:
        return False
    if _user32.GetForegroundWindow() == hwnd:
        return True

    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    if _user32.GetForegroundWindow() == hwnd:
        return True

    target = _user32.GetWindowThreadProcessId(hwnd, None)
    current = ctypes.windll.kernel32.GetCurrentThreadId()
    fore = _user32.GetWindowThreadProcessId(
        _user32.GetForegroundWindow(), None)
    threads = {target, fore} - {current, 0}
    for t in threads:
        _user32.AttachThreadInput(current, t, True)
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        for t in threads:
            _user32.AttachThreadInput(current, t, False)
    time.sleep(settle)
    if _user32.GetForegroundWindow() == hwnd:
        return True
    # Windows refuses SetForegroundWindow to a process launched in the
    # background; a real click is always allowed to activate a window.
    if _click_title_bar(hwnd):
        time.sleep(settle)
    return _user32.GetForegroundWindow() == hwnd


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _click_title_bar(hwnd) -> bool:
    """One left click on the window's title bar, never on the game itself
    (a click there would walk the character). False when it has none."""
    frame, client = _Rect(), _Point(0, 0)
    if (not _user32.GetWindowRect(hwnd, ctypes.byref(frame))
            or not _user32.ClientToScreen(hwnd, ctypes.byref(client))):
        return False
    top = max(frame.top, 0)
    if client.y - top < 6:
        return False
    x, y = (frame.left + frame.right) // 2, (top + client.y) // 2
    calibration.halt_if_stopped()
    was = _Point()
    _user32.GetCursorPos(ctypes.byref(was))
    _user32.SetCursorPos(x, y)
    time.sleep(0.05)
    down, up = _IN["MOUSEEVENTF_LEFTDOWN"], _IN["MOUSEEVENTF_LEFTUP"]
    for flags in (down, up):
        event = _Input(type=INPUT_MOUSE,
                       u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))
        _user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_Input))
        time.sleep(0.05)
    _user32.SetCursorPos(was.x, was.y)
    print(f"  the game would not come forward; clicked its title bar at "
          f"[{x}, {y}]")
    return True


def open_inventory() -> None:
    if not focus_game():
        raise RuntimeError(
            f"could not bring the {GAME_TITLE!r} window to the foreground.")
    press(VK_I)


if __name__ == "__main__":
    open_inventory()
    print("pressed I")
