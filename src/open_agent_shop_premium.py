import ctypes
import time

import calibration
from open_inventory import (VK_I, _Input, _InputUnion, _MouseInput, _user32,
                            focus_game, press)

CAL = calibration.load()

_FACTS = CAL["game_facts"]
GRID_SIZE = _FACTS["grid_size"]
AGENT_SHOP_TAB = _FACTS["agent_shop_tab"]
AGENT_SHOP_SLOT = tuple(_FACTS["agent_shop_slot"])

ACTION_GAP = CAL["timing"]["action_gap"]


CALIBRATED_ALZ = tuple(CAL["inventory"]["alz_box"])
PANEL_MOVED_SLACK = CAL["detect"]["panel_moved_slack"]
MIN_PLAUSIBLE_BALANCE = CAL["detect"]["min_plausible_balance"]


def grab():
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def panel_open(image=None, verbose: bool = True) -> bool:
    image = image if image is not None else grab()
    value = calibration.read_digits(image, CALIBRATED_ALZ)
    if value is None:
        return False
    if value < MIN_PLAUSIBLE_BALANCE:
        if verbose:
            print(f"  the balance box reads {value}, below "
                  f"{MIN_PLAUSIBLE_BALANCE} -- treating the panel as shut.")
        return False
    return True


def slot_point(row: int, col: int) -> "tuple[int, int]":
    key = f"{row}x{col}"
    try:
        return tuple(CAL["inventory"]["slots"][key])
    except KeyError:
        raise ValueError(f"slot {key} is not in calibration.json") from None


def tab_point(tab: int) -> "tuple[int, int]":
    try:
        return tuple(CAL["inventory"]["tabs"][str(tab)])
    except KeyError:
        raise ValueError(
            f"tab {tab} is not in calibration.json, which has "
            f"{sorted(CAL['inventory']['tabs'])}") from None


def favourite_point(slot: int) -> "tuple[int, int]":
    favs = CAL["shop"]["favourites"]
    if not 1 <= slot <= len(favs):
        raise ValueError(f"favourite {slot} is outside 1..{len(favs)}")
    return tuple(favs[slot - 1])


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
    _user32.SetCursorPos(int(x), int(y))
    _user32.SendInput(1, ctypes.byref(_mouse(down)), ctypes.sizeof(_Input))
    try:
        pass
    finally:
        _user32.SendInput(1, ctypes.byref(_mouse(up)), ctypes.sizeof(_Input))
    time.sleep(settle)


def click(x: int, y: int, settle: float = ACTION_GAP) -> None:
    _button(MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, x, y, settle)


def right_click(x: int, y: int, settle: float = 0.0) -> None:
    _button(MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, x, y, settle)


def ensure_inventory_open(verbose: bool = True) -> None:
    if panel_open():
        if verbose:
            print("  inventory already open")
        return

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
