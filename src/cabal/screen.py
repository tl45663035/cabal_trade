"""Talking to Windows and to the Cabal client: capture, window, input.

Spec: screen.md

Nothing here knows what a Trade window is. It knows how to photograph the
screen, find the game's window, and press things. Everything above this module
is about Cabal; everything below it is about Windows.

DPI IS THE FIRST THING, ALWAYS. Windows lies to a process that has not
declared itself DPI-aware: it reports a virtual screen size and silently
rescales coordinates, so a capture and a click disagree about where a pixel
is. The capture comes back at one size and the cursor lands somewhere else,
which looks exactly like bad calibration and is not. make_dpi_aware() runs
before any capture and before any move.
"""

from __future__ import annotations

import ctypes
import io
import time
from ctypes import wintypes

import mss
import mss.tools
from PIL import Image

# The substring that identifies the game's window title. A single point of
# failure for the whole bootstrap: no match means no client rect, which means
# no calibration, which means every coordinate falls back to a reference that
# is wrong on this machine.
GAME_TITLE_HINT = "PlayCabal"

# After any synthetic input, how long to let the client react before the next
# one. The game drops input sent faster than it can process.
INPUT_COOLDOWN = 0.08

_dpi_done = False


def make_dpi_aware() -> None:
    """Declare this process DPI-aware. Idempotent, and required before all else."""
    global _dpi_done
    if _dpi_done:
        return
    try:
        # PER_MONITOR_AWARE_V2. The modern context, and the only one that gets
        # per-monitor scaling right on a mixed-DPI desktop.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:  # noqa: BLE001 - older Windows
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001 - older still
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:  # noqa: BLE001 - nothing more to try
                pass
    _dpi_done = True


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------

def screen_size() -> "tuple[int, int]":
    """The PRIMARY monitor's size in real pixels."""
    make_dpi_aware()
    with mss.mss() as sct:
        mon = sct.monitors[1]
        return (mon["width"], mon["height"])


def grab() -> Image.Image:
    """A fresh screenshot of the primary monitor.

    The primary monitor, not the virtual desktop. On a multi-monitor machine
    the union of all monitors has its own origin, and capturing it shifts every
    pixel by that origin -- so a coordinate measured in the capture no longer
    matches the coordinate a click uses.
    """
    make_dpi_aware()
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
    png = mss.tools.to_png(shot.rgb, shot.size)
    return Image.open(io.BytesIO(png)).convert("RGB")


# --------------------------------------------------------------------------
# The game window
# --------------------------------------------------------------------------

def find_game_window() -> "int | None":
    """The handle of the game's visible top-level window, or None."""
    make_dpi_aware()
    u32 = ctypes.windll.user32
    found: list[int] = []
    hint = GAME_TITLE_HINT.casefold()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, buf, length + 1)
        if hint in buf.value.casefold():
            found.append(hwnd)
            return False
        return True

    try:
        u32.EnumWindows(each, 0)
    except Exception:  # noqa: BLE001 - diagnostic path only
        return None
    return found[0] if found else None


def client_rect() -> "tuple[int, int, int, int] | None":
    """The game's CLIENT area in screen pixels, or None.

    The client area, not the window rect: the title bar and borders are not
    part of the rendered UI, and including them shifts every derived region by
    the height of the title bar.
    """
    hwnd = find_game_window()
    if not hwnd:
        return None
    make_dpi_aware()
    u32 = ctypes.windll.user32
    try:
        rect = wintypes.RECT()
        if not u32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
            return None
        point = wintypes.POINT(rect.left, rect.top)
        if not u32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(point)):
            return None
        return (point.x, point.y,
                point.x + (rect.right - rect.left),
                point.y + (rect.bottom - rect.top))
    except Exception:  # noqa: BLE001 - a missing rect is not a crash
        return None


def focus_game(timeout: float = 3.0) -> bool:
    """Bring the client to the foreground. False if it would not come.

    Clicks are delivered to whatever holds the foreground, so a click sent
    while another window is on top goes to that window instead. Verified by
    reading the foreground back rather than trusting the call: Windows returns
    success from SetForegroundWindow in cases where it has quietly done
    nothing.
    """
    hwnd = find_game_window()
    if not hwnd:
        return False
    u32 = ctypes.windll.user32
    deadline = time.monotonic() + timeout
    while True:
        if u32.GetForegroundWindow() == hwnd:
            return True
        try:
            u32.SetForegroundWindow(ctypes.c_void_p(hwnd))
        except Exception:  # noqa: BLE001 - retried below
            pass
        if time.monotonic() >= deadline:
            return u32.GetForegroundWindow() == hwnd
        time.sleep(0.15)


# --------------------------------------------------------------------------
# Input
# --------------------------------------------------------------------------

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
KEYEVENTF_KEYUP = 0x0002
VK_ESCAPE = 0x1B


def move_mouse(x: int, y: int) -> bool:
    """Put the cursor on a screen pixel. False if Windows refused.

    The usual cause of a refusal is UIPI: if the game runs elevated and this
    process does not, Windows silently drops injected input while the game
    holds the foreground. It is not a calibration problem and no amount of
    re-measuring fixes it -- the script has to run as Administrator too.
    """
    make_dpi_aware()
    u32 = ctypes.windll.user32
    u32.SetCursorPos.restype = ctypes.c_int
    moved = bool(u32.SetCursorPos(int(x), int(y)))
    if moved:
        time.sleep(INPUT_COOLDOWN)
    return moved


def cursor_position() -> "tuple[int, int]":
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return (point.x, point.y)


def click(x: int, y: int, right: bool = False) -> bool:
    """Move to (x, y) and press. False if the move was refused.

    APPROACHED, NOT TELEPORTED ONTO. A move to the pixel the cursor already
    occupies raises no mouse-move event, so a control that arms on hover is
    never armed and the click does nothing. Callers that care about hover
    should move somewhere else first; this function guarantees only that the
    cursor is on the target when the button goes down.
    """
    if not move_mouse(x, y):
        return False
    down = MOUSEEVENTF_RIGHTDOWN if right else MOUSEEVENTF_LEFTDOWN
    up = MOUSEEVENTF_RIGHTUP if right else MOUSEEVENTF_LEFTUP
    u32 = ctypes.windll.user32
    u32.mouse_event(down, 0, 0, 0, 0)
    time.sleep(0.03)
    u32.mouse_event(up, 0, 0, 0, 0)
    time.sleep(INPUT_COOLDOWN)
    return True


def press_escape() -> None:
    """Tap Escape. Closes the topmost game panel."""
    u32 = ctypes.windll.user32
    u32.keybd_event(VK_ESCAPE, 0, 0, 0)
    time.sleep(0.03)
    u32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(INPUT_COOLDOWN)


def wait_until(predicate, timeout: float = 5.0, poll: float = 0.2) -> bool:
    """Poll `predicate` until it is true or `timeout` expires."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 - a predicate that throws is a False
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
