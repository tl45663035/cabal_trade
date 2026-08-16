"""Press I.

    py src/open_inventory.py

That is all it does. It does not look at the screen, does not check whether
the panel opened, and does not care which window has focus -- the keystroke
goes wherever Windows is currently sending them.
"""
import ctypes
import time

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


def open_inventory() -> None:
    press(VK_I)


if __name__ == "__main__":
    open_inventory()
    print("pressed I")
