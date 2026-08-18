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


def press(vk: int) -> None:
    sent = _user32.SendInput(
        1, ctypes.byref(_event(vk, up=False)), ctypes.sizeof(_Input))
    try:
        if sent != 1:
            err = ctypes.get_last_error()
            raise OSError(err, f"SendInput sent {sent} of 1 events "
                               f"(GetLastError {err}). Nothing was pressed.")
        time.sleep(KEY_HOLD)
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
    return _user32.GetForegroundWindow() == hwnd


def open_inventory() -> None:
    if not focus_game():
        raise RuntimeError(
            f"could not bring the {GAME_TITLE!r} window to the foreground. "
            f"Not pressing I -- it would go to whatever is in front instead.")
    press(VK_I)


if __name__ == "__main__":
    open_inventory()
    print("pressed I")
