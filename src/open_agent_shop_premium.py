"""Open the Agent Shop with the key: inventory -> tab VIII -> right-click (1,7).

    py src/open_agent_shop_premium.py

PREMIUM ONLY, which is what the name says. Slot (1,7) on tab VIII holds the
Agent Shop key, and right-clicking it opens the shop from anywhere. An account
without the key has to walk to the NPC and talk to her instead -- a different
routine, not a fallback inside this one.
"""
import ctypes
import time

import calibration
from open_inventory import (VK_I, _Input, _InputUnion, _MouseInput, _user32,
                            focus_game, press)

# --------------------------------------------------------------------------
# Everything positional comes from calibration.json
# --------------------------------------------------------------------------
#
# Not one screen coordinate is written in this file. `py src/calibration.py`
# measures them on the live screen and writes them down; this reads them.
#
# WHAT IS STILL A CONSTANT HERE, and why none of it is a location:
#   GRID_SIZE          the inventory is 8x8. A fact about the game.
#   AGENT_SHOP_TAB     which tab the key lives on, and which slot. Facts about
#   AGENT_SHOP_SLOT    the BAG, not the screen -- moving the key changes these,
#                      moving the window does not.
#   ACTION_GAP         a duration.
#   MOUSEEVENTF_*      Windows API.
CAL = calibration.load()

GRID_SIZE = 8
AGENT_SHOP_TAB = 8
AGENT_SHOP_SLOT = (1, 7)

# The gap between one action and the next: press I, wait, click the tab, wait,
# right-click the key. One number rather than a different invented value at
# each step. Nothing follows the final right-click, so it is not waited on.
ACTION_GAP = 0.05

# I IS A TOGGLE, so "open the inventory" cannot be a blind press: with the
# panel already up, pressing I shuts it and every click below lands on the game
# world. The state has to be read -- and it is read with the same thresholds
# calibration measured it with, taken from the file, so the two cannot drift.
_D = CAL["alz_detect"]
ALZ_SEARCH = tuple(_D["search"])
ALZ_BRIGHT = _D["bright"]
ALZ_SATURATION = _D["saturation"]
ALZ_MIN_PIXELS = _D["min_pixels"]
ALZ_LINE_HALF = _D["line_half"]

# Where the balance was when the screen was measured. The positions below are
# absolute, so they are only valid while the panel has not moved; panel_open()
# checks this and refuses if it has.
CALIBRATED_ALZ = tuple(CAL["inventory"]["alz_box"])


def grab():
    """One screenshot of the primary monitor."""
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def panel_open(image=None, verbose: bool = True) -> bool:
    """Is the Inventory panel up, AND still where it was calibrated?

    Both halves matter. Every position here is absolute, measured once, so it
    is valid only while the panel has not moved. This finds the Alz balance the
    way calibration.py found it and checks it is within 30px of where it was --
    so a dragged panel reads as "not open" and the caller refuses, rather than
    clicking at a stale coordinate.
    """
    image = image if image is not None else grab()
    crop = image.crop(ALZ_SEARCH)
    px = crop.load()
    xs, ys = [], []
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > ALZ_BRIGHT and hi - lo > ALZ_SATURATION:
                xs.append(x)
                ys.append(y)
    if len(xs) < ALZ_MIN_PIXELS:
        return False
    rows = {}
    for y in ys:
        rows[y] = rows.get(y, 0) + 1
    peak = max(rows, key=rows.get)
    keep = [(x, y) for x, y in zip(xs, ys) if abs(y - peak) <= ALZ_LINE_HALF]
    if len(keep) < ALZ_MIN_PIXELS:
        return False
    right = ALZ_SEARCH[0] + max(x for x, _ in keep)
    top = ALZ_SEARCH[1] + min(y for _, y in keep)
    if abs(right - CALIBRATED_ALZ[2]) > 30 or abs(top - CALIBRATED_ALZ[1]) > 30:
        if verbose:
            print(f"  the Inventory panel is open but has MOVED: balance at "
                  f"({right}, {top}), calibrated at ({CALIBRATED_ALZ[2]}, "
                  f"{CALIBRATED_ALZ[1]}). Re-run py src/calibration.py")
        return False
    return True


def slot_point(row: int, col: int) -> "tuple[int, int]":
    """Screen centre of inventory slot (row, col), as measured."""
    key = f"{row}x{col}"
    try:
        return tuple(CAL["inventory"]["slots"][key])
    except KeyError:
        raise ValueError(f"slot {key} is not in calibration.json") from None


def tab_point(tab: int) -> "tuple[int, int]":
    """Screen centre of inventory tab `tab`, as measured."""
    try:
        return tuple(CAL["inventory"]["tabs"][str(tab)])
    except KeyError:
        raise ValueError(
            f"tab {tab} is not in calibration.json, which has "
            f"{sorted(CAL['inventory']['tabs'])}") from None


def favourite_point(slot: int) -> "tuple[int, int]":
    """Screen centre of favourite slot `slot` (1-based), as measured."""
    favs = CAL["shop"]["favourites"]
    if not 1 <= slot <= len(favs):
        raise ValueError(f"favourite {slot} is outside 1..{len(favs)}")
    return tuple(favs[slot - 1])


# --------------------------------------------------------------------------
# Mouse
# --------------------------------------------------------------------------
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


def _mouse(flags: int) -> _Input:
    return _Input(type=INPUT_MOUSE,
                  u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _button(down: int, up: int, x: int, y: int, settle: float) -> None:
    """Move, press, release. The release is in a finally so it cannot stick.

    NO SLEEP BETWEEN THE MOVE AND THE PRESS, AND NONE IN THE HOLD, at the
    operator's instruction. There were two -- 60ms after SetCursorPos and 30ms
    between down and up -- and I invented both. They cost 90ms on EVERY click
    regardless of the settle, which is why right_click still took 95ms after
    its settle was removed.

    What they were guarding, so it is on record if a click ever misses: the
    move-wait covers the client not having registered the new cursor position
    when the button-down arrives, which would land the click at the OLD
    position. That is a wrong-click, the expensive kind. The hold covers a
    press too brief to register, which merely drops the click.
    """
    _user32.SetCursorPos(int(x), int(y))
    _user32.SendInput(1, ctypes.byref(_mouse(down)), ctypes.sizeof(_Input))
    try:
        pass
    finally:
        _user32.SendInput(1, ctypes.byref(_mouse(up)), ctypes.sizeof(_Input))
    time.sleep(settle)


def click(x: int, y: int, settle: float = ACTION_GAP) -> None:
    """Left-click, then wait ACTION_GAP before whatever comes next."""
    _button(MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, x, y, settle)


def right_click(x: int, y: int, settle: float = 0.0) -> None:
    """No settle. Nothing reads after the right-click; the function returns.

    It was 0.6s, which measured 695ms of the 1,539ms this script took and
    delayed only the exit.
    """
    _button(MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, x, y, settle)


# --------------------------------------------------------------------------
def ensure_inventory_open(verbose: bool = True) -> None:
    """Leave the Inventory panel open, where the calibration says it is."""
    if panel_open():
        if verbose:
            print("  inventory already open")
        return

    press(VK_I)
    time.sleep(ACTION_GAP)
    # The real check is this read, not the wait: if the panel is not there,
    # the script refuses rather than clicking into the game world. Polled on
    # this screen the panel is up within one screenshot (~30ms).
    if not panel_open():
        raise RuntimeError(
            "pressed I but the Inventory panel is not open where "
            "calibration.json says it is. Not clicking: those coordinates "
            "would be the game world.")
    if verbose:
        print("  inventory opened")


def open_agent_shop(verbose: bool = True) -> None:
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")

    ensure_inventory_open(verbose=verbose)

    tab = tab_point(AGENT_SHOP_TAB)
    if verbose:
        print(f"  tab {AGENT_SHOP_TAB} at {tab}")
    click(*tab)

    # The anchor is re-read after the tab click rather than reused. Switching
    # tabs redraws the panel, and if that click missed, the slot below is the
    # wrong tab's -- right-clicking it would use whatever item is there.
    if not panel_open():
        raise RuntimeError(
            f"the Inventory panel is gone or has moved after clicking tab "
            f"{AGENT_SHOP_TAB}. Not right-clicking.")

    row, col = AGENT_SHOP_SLOT
    point = slot_point(row, col)
    if verbose:
        print(f"  right-clicking slot ({row},{col}) at {point}")
    right_click(*point)


if __name__ == "__main__":
    open_agent_shop()
    print("done")
