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
# NOTHING is defined in this file -- not the positions, not the Windows API
# numbers, not the durations, not the facts about the bag. All of it is read
# from calibration.json, so a change lands in every script at once.
CAL = calibration.load()

_FACTS = CAL["game_facts"]
GRID_SIZE = _FACTS["grid_size"]
AGENT_SHOP_TAB = _FACTS["agent_shop_tab"]
AGENT_SHOP_SLOT = tuple(_FACTS["agent_shop_slot"])

# The gap between one action and the next: press I, wait, click the tab, wait,
# right-click the key. One number, and it lives in calibration.json, so it is
# one number for EVERY script rather than a copy per file. Nothing follows the
# final right-click, so it is not waited on.
ACTION_GAP = CAL["timing"]["action_gap"]

# I IS A TOGGLE, so "open the inventory" cannot be a blind press: with the
# panel already up, pressing I shuts it and every click below lands on the game
# world. The state has to be read -- and it is read with the same thresholds
# calibration measured it with, taken from the file, so the two cannot drift.
# The detection thresholds are NOT read here any more: calibration.find_alz
# owns them, and owning them in one place is the point. Only where the balance
# WAS is needed, to notice the panel having moved.

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

    THE BALANCE IS FOUND BY calibration.find_alz, NOT BY A COPY OF IT HERE.
    There used to be a second implementation in this file, and the two drifted
    the moment one was fixed: calibration's gained shape checks -- reject a box
    that fills the search width, reject an implausible height -- because the 3D
    world passes a bare "enough bright saturated pixels" test. This copy did
    not, so on 2026-08-16, with the panel SHUT, calibration correctly reported
    "Inventory already closed" and this reported "inventory already open" one
    second later. It then skipped pressing I and clicked the game world.

    One implementation, one definition of open.

    The second half is this file's own concern: the positions here are
    absolute, so they are valid only while the panel has not moved. A balance
    found more than a slot's width from where it was measured reads as "not
    open", and the caller refuses rather than clicking a stale coordinate.
    """
    image = image if image is not None else grab()
    box = calibration.find_alz(image)
    if box is None:
        return False
    right, top = box[2], box[1]
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
_IN = CAL["input"]
INPUT_MOUSE = _IN["INPUT_MOUSE"]
MOUSEEVENTF_LEFTDOWN = _IN["MOUSEEVENTF_LEFTDOWN"]
MOUSEEVENTF_LEFTUP = _IN["MOUSEEVENTF_LEFTUP"]
MOUSEEVENTF_RIGHTDOWN = _IN["MOUSEEVENTF_RIGHTDOWN"]
MOUSEEVENTF_RIGHTUP = _IN["MOUSEEVENTF_RIGHTUP"]


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

    # PRESS, CHECK, PRESS AGAIN IF WRONG. I is a toggle, and a run that failed
    # part-way leaves the panel open, so the next run's press closes what it
    # meant to open. Two presses cover both starting states.
    for attempt in (1, 2):
        press(VK_I)
        time.sleep(ACTION_GAP)
        if panel_open(verbose=(attempt == 2)):
            if verbose:
                print("  inventory opened"
                      + ("  (took two presses -- it had been left open)"
                         if attempt == 2 else ""))
            return
    raise RuntimeError(
        "pressed I twice and the Inventory panel is not open where "
        "calibration.json says it is. Not clicking: those coordinates would "
        "be the game world.")


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
