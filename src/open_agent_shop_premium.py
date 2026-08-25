import time

import calibration
from open_inventory import (VK_I, _Input, _InputUnion, _MouseInput,
                            focus_game, press)

CAL = calibration.load_shared()

_FACTS = CAL["game_facts"]
AGENT_SHOP_TAB = _FACTS["agent_shop_tab"]
AGENT_SHOP_SLOT = tuple(_FACTS["agent_shop_slot"])

ACTION_GAP = CAL["timing"]["action_gap"]
DIALOG_TIMEOUT = CAL["timing"]["dialog_timeout"]
POLL_GAP = CAL["timing"]["poll_gap"]
LOAD_ATTEMPTS = CAL["detect"]["load_attempts"]


MIN_PLAUSIBLE_BALANCE = CAL["detect"]["min_plausible_balance"]


def grab():
    import mss
    from PIL import Image
    with mss.MSS() as sct:
        raw = sct.grab(sct.monitors[1])
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def panel_open(image=None, verbose: bool = True) -> bool:
    image = image if image is not None else grab()
    value = calibration.read_balance_from(image)
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
        return tuple(calibration.load()["inventory"]["slots"][key])
    except KeyError:
        raise ValueError(f"slot {key} is not in calibration.json") from None


def tab_point(tab: int) -> "tuple[int, int]":
    try:
        return tuple(calibration.load()["inventory"]["tabs"][str(tab)])
    except KeyError:
        raise ValueError(
            f"tab {tab} is not in calibration.json, which has "
            f"{sorted(calibration.load()['inventory']['tabs'])}") from None


def favourite_point(slot: int) -> "tuple[int, int]":
    favs = calibration.load()["shop"]["favourites"]
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


def click(x: int, y: int, settle: float = None) -> None:
    calibration.click(x, y, settle=settle)


def right_click(x: int, y: int, settle: float = None) -> None:
    calibration.right_click(x, y, settle=settle)


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
        "pressed I twice; the Inventory panel is not open where "
        "calibration.json says it is.")


def open_agent_shop(verbose: bool = True) -> None:
    if not focus_game():
        raise RuntimeError("could not bring the game to the foreground.")

    row, col = AGENT_SHOP_SLOT
    for attempt in range(1, LOAD_ATTEMPTS + 1):
        ensure_inventory_open(verbose=verbose)

        tab = tab_point(AGENT_SHOP_TAB)
        if verbose:
            print(f"  tab {AGENT_SHOP_TAB} at {tab}")
        click(*tab)

        if not panel_open():
            calibration.snap("inventory_gone_after_tab")
            raise RuntimeError(
                f"the Inventory panel is not open after pressing I and "
                f"clicking tab {AGENT_SHOP_TAB}. The game is showing "
                f"something else -- a loading screen, a transfer, or another "
                f"window has the focus. Not right-clicking into the world.")

        point = slot_point(row, col)
        if verbose:
            print(f"  right-clicking slot ({row},{col}) at {point} "
                  f"(attempt {attempt}/{LOAD_ATTEMPTS})")
        right_click(*point)

        deadline = time.monotonic() + DIALOG_TIMEOUT
        while time.monotonic() < deadline:
            if calibration._trade_window_open():
                return
            time.sleep(POLL_GAP)
        calibration.snap(f"no_trade_window_{attempt}")
        if verbose:
            print(f"  no Trade window after attempt {attempt}; the panel was "
                  f"on another tab when the key was right-clicked. Selecting "
                  f"tab {AGENT_SHOP_TAB} again.")


if __name__ == "__main__":
    open_agent_shop()
    print("done")
