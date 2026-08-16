"""Open the Agent Shop: inventory -> tab VIII -> right-click slot (1,7).

    py src/open_agent_shop.py

Slot (1,7) on tab VIII holds the Agent Shop key. Right-clicking it opens the
shop from anywhere, without walking to the NPC.
"""
import ctypes
import time

from open_inventory import (VK_I, _Input, _InputUnion, _MouseInput, _user32,
                            focus_game, press)

# --------------------------------------------------------------------------
# Is the Inventory panel open, and where is it?
# --------------------------------------------------------------------------
#
# I IS A TOGGLE, so "open the inventory" cannot be a blind press: if the panel
# is already up, pressing I shuts it and every click below lands on the game
# world instead. The state has to be read.
#
# Read from the Alz balance, because it is the one thing on the panel that is
# bright and saturated -- the figure is orange, or green just after it changes,
# against a dark panel. Counting pixels that are BOTH bright and colourful in
# the balance's box separates the two states by 20x, measured on this screen:
#
#     panel closed   24 and 20 bright pixels   (0.2% of the box)
#     panel open    545 bright pixels          (5.0%)
#
# The threshold sits between those, nowhere near either. This is deliberately
# not a "how much variance is in the region" test -- that kind saturates on
# game art and is what made trade.py report all 64 inventory slots occupied on
# an empty tab.
ALZ_REGION = (2330, 872, 2525, 928)
ALZ_BRIGHT = 110          # a channel this high counts as bright
ALZ_SATURATION = 45       # ...and this far from the dimmest channel
PANEL_OPEN_PIXELS = 150   # between the measured 24 (closed) and 545 (open)

# The panel anchor, as an offset from the Alz digits' own box. Taking it from
# the digits rather than from a fixed screen point means the panel can be
# dragged and the slots still resolve.
ALZ_TO_ANCHOR = (-241, -718)   # applied to (box right, box top)

# Slots and tabs, measured from that anchor.
SLOT_ONE_OFFSET = (-261, 120)
SLOT_PITCH = (73.9, 74.1)
TAB_ONE_OFFSET = (-281, 52)
TAB_PITCH = 69.2
GRID_SIZE = 8

AGENT_SHOP_TAB = 8
AGENT_SHOP_SLOT = (1, 7)


def grab():
    """One screenshot of the primary monitor."""
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def find_panel(image=None) -> "tuple[int, int] | None":
    """The Inventory panel's anchor, or None when the panel is shut."""
    image = image if image is not None else grab()
    crop = image.crop(ALZ_REGION)
    px = crop.load()
    xs, ys = [], []
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > ALZ_BRIGHT and hi - lo > ALZ_SATURATION:
                xs.append(x)
                ys.append(y)
    if len(xs) < PANEL_OPEN_PIXELS:
        return None
    right, top = ALZ_REGION[0] + max(xs), ALZ_REGION[1] + min(ys)
    return (right + ALZ_TO_ANCHOR[0], top + ALZ_TO_ANCHOR[1])


def slot_point(anchor, row: int, col: int) -> "tuple[int, int]":
    if not (1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE):
        raise ValueError(f"slot ({row},{col}) is outside the grid")
    return (round(anchor[0] + SLOT_ONE_OFFSET[0] + SLOT_PITCH[0] * (col - 1)),
            round(anchor[1] + SLOT_ONE_OFFSET[1] + SLOT_PITCH[1] * (row - 1)))


def tab_point(anchor, tab: int) -> "tuple[int, int]":
    if not 1 <= tab <= GRID_SIZE:
        raise ValueError(f"tab {tab} is outside I..VIII")
    return (round(anchor[0] + TAB_ONE_OFFSET[0] + TAB_PITCH * (tab - 1)),
            round(anchor[1] + TAB_ONE_OFFSET[1]))


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


def click(x: int, y: int, settle: float = 0.10) -> None:
    """100ms, at the operator's instruction. It was 350ms, invented.

    Something has to follow a tab click: the panel redraws, and find_panel
    below reads it. Measured on this screen, the Inventory panel reaches its
    new state within one screenshot -- 30ms polled, which is what a grab()
    costs, so it was already there before the first poll finished.
    """
    _button(MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, x, y, settle)


def right_click(x: int, y: int, settle: float = 0.0) -> None:
    """No settle. Nothing reads after the right-click; the function returns.

    It was 0.6s, which measured 695ms of the 1,539ms this script took and
    delayed only the exit.
    """
    _button(MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, x, y, settle)


# --------------------------------------------------------------------------
def ensure_inventory_open(verbose: bool = True) -> "tuple[int, int]":
    """Leave the Inventory panel open and return its anchor."""
    anchor = find_panel()
    if anchor is not None:
        if verbose:
            print(f"  inventory already open, anchor {anchor}")
        return anchor

    press(VK_I)
    # 100ms, at the operator's instruction. It was 0.9s, invented -- and it
    # was the whole remaining spread in this script's timing, since it runs
    # only when the inventory was shut. Polled on this screen, the panel
    # reaches its new state in 30ms, which is what a grab() costs, so it was
    # already up before the first poll finished. find_panel below is the real
    # check: if the panel is not there, this refuses rather than clicking.
    time.sleep(0.1)
    anchor = find_panel()
    if anchor is None:
        raise RuntimeError(
            "pressed I but the Inventory panel did not appear. Not clicking: "
            "without the panel these coordinates are the game world, and a "
            "right-click there is not what this script means to do.")
    if verbose:
        print(f"  inventory opened, anchor {anchor}")
    return anchor


def open_agent_shop(verbose: bool = True) -> None:
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")

    anchor = ensure_inventory_open(verbose=verbose)

    tab = tab_point(anchor, AGENT_SHOP_TAB)
    if verbose:
        print(f"  tab {AGENT_SHOP_TAB} at {tab}")
    click(*tab)

    # The anchor is re-read after the tab click rather than reused. Switching
    # tabs redraws the panel, and if that click missed, the slot below is the
    # wrong tab's -- right-clicking it would use whatever item is there.
    anchor = find_panel()
    if anchor is None:
        raise RuntimeError(
            f"the Inventory panel is gone after clicking tab "
            f"{AGENT_SHOP_TAB}. Not right-clicking.")

    row, col = AGENT_SHOP_SLOT
    point = slot_point(anchor, row, col)
    if verbose:
        print(f"  right-clicking slot ({row},{col}) at {point}")
    right_click(*point)


if __name__ == "__main__":
    open_agent_shop()
    print("done")
