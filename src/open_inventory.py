"""Focus the game, then press I.

    py src/open_inventory.py

It does not look at the screen and does not check whether the panel opened.

Focus is here because without it the key goes to whatever window happens to be
in front: the first run printed "pressed I" and typed an `i` into the
PowerShell prompt that launched it, with the game untouched.
"""
import ctypes
import time

import calibration

# Scan code, not just the virtual key.
#
# The Cabal client reads the keyboard through raw input, which looks at the
# SCAN code and ignores virtual-key-only events. A keybd_event-style press with
# wScan left at 0 reaches Notepad and does nothing here.
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

VK_I = 0x49

# use_last_error=True, or GetLastError() reads whatever unrelated call ran
# last. ctypes.windll does not arm it, so the first cut reported "[Errno 0]"
# for a failure that had a real code waiting.
_user32 = ctypes.WinDLL("user32", use_last_error=True)


class _MouseInput(ctypes.Structure):
    """Never sent here. Present because it SIZES THE UNION.

    SendInput validates cbSize against sizeof(INPUT) exactly and returns 0 --
    sending nothing -- when it does not match. INPUT's union is as large as its
    biggest member, and that is MOUSEINPUT (32 bytes) not KEYBDINPUT (24).
    Omitting this field to keep the file small made sizeof(INPUT) 32 instead of
    40, and every keystroke was silently refused.
    """
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


# Checked at import rather than trusted. If a future edit changes the structs,
# this says so here instead of at a keystroke that quietly does nothing.
assert ctypes.sizeof(_Input) == 40, (
    f"sizeof(INPUT) is {ctypes.sizeof(_Input)}, must be 40 on 64-bit Windows; "
    f"SendInput refuses anything else")


def _event(vk: int, up: bool) -> _Input:
    scan = _user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  u=_InputUnion(ki=_KeyInput(vk, scan, flags, 0, None)))


def press(vk: int) -> None:
    """Tap one key. The release is in a finally, so the key cannot stick.

    A key left down starts Windows auto-repeat, which then fires into whatever
    gains focus next.
    """
    sent = _user32.SendInput(
        1, ctypes.byref(_event(vk, up=False)), ctypes.sizeof(_Input))
    try:
        if sent != 1:
            err = ctypes.get_last_error()
            raise OSError(err, f"SendInput sent {sent} of 1 events "
                               f"(GetLastError {err}). Nothing was pressed.")
        time.sleep(0.02)
    finally:
        _user32.SendInput(
            1, ctypes.byref(_event(vk, up=True)), ctypes.sizeof(_Input))


# FROM calibration.json, not typed here. This is the only environment-specific
# value in this file; everything else is a Windows API constant or a virtual
# key code, which are facts about Windows rather than about this screen.
GAME_TITLE = calibration.load()["game"]["title_hint"]
VK_MENU = 0x12          # Alt


def find_game_window() -> "int | None":
    """HWND of the Cabal window, or None.

    Matched on "PlayCabal", NOT on "Cabal". This project lives in a folder
    called Cabal, so an editor with it open is titled "... - Cabal - Visual
    Studio Code" and a looser match picks the editor instead of the game.
    """
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


def focus_game(settle: float = 0.35) -> bool:
    """Bring the game to the foreground. True if it ended up there."""
    hwnd = find_game_window()
    if hwnd is None:
        return False
    if _user32.GetForegroundWindow() == hwnd:
        return True

    # SW_RESTORE only when actually minimised: calling it on a maximised
    # window un-maximises it, which moves everything.
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)
    _user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    if _user32.GetForegroundWindow() == hwnd:
        return True

    # SetForegroundWindow is refused unless the caller already owns the
    # foreground. Borrow the foreground and target threads' input state so
    # this call counts as owning it.
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
