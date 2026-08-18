import re
import sys
import time

import calibration
import get_alz
import get_price
import row_model
import open_agent_shop_premium as shop
import open_inventory as inv

_SHARED = calibration.load_shared()
_T = _SHARED["timing"]
ACTION_GAP = _T["action_gap"]
TAB_SETTLE = _T["tab_settle"]
_ROW = re.compile(_SHARED["text"]["purchase_row"])
CAPACITY = _SHARED["game_facts"]["shop_capacity"]
VISIBLE = _SHARED["game_facts"]["shop_visible"]


class NotReady(Exception):
    pass


def calibrated():
    try:
        calibration.load(force=True)
        return True
    except RuntimeError:
        return False


def initialise(verbose=True):
    if not inv.focus_game():
        raise NotReady("could not bring the game to the foreground.")
    if not calibrated():
        raise NotReady(
            "this screen is not calibrated. Run py src/calibration.py first; "
            "the driver will not measure the game itself.")
    cal = calibration.load()
    if verbose:
        print(f"  calibrated for {cal['resolution']}, measured "
              f"{cal.get('measured_at')}")
    if not calibration._trade_window_open():
        if verbose:
            print("  opening the Agent Shop")
        shop.open_agent_shop(verbose=verbose)
        time.sleep(TAB_SETTLE)
    return cal


def register_tab(verbose=True):
    calibration.click(*calibration.load()["shop"]["register_tab"])
    time.sleep(TAB_SETTLE)
    calibration.park()
    time.sleep(ACTION_GAP)


def balance(verbose=True):
    return get_alz.read_balance()


def market(slot, verbose=True):
    return get_price.get_price(slot, verbose=verbose)


def seed(verbose=True):
    register_tab(verbose=verbose)
    model = row_model.RowModel().seed({}, top=1)
    found = {}
    for index in range(1, row_model.MAX_TOP + 1):
        model.scroll_to(index, verbose=False)
        time.sleep(ACTION_GAP)
        text = row_model.read_row_one()
        if row_model.row_one_is_empty(text):
            continue
        row = _row_from(text)
        if row is None:
            if verbose:
                print(f"    {index:2}  UNREAD {text[:56]!r}")
            continue
        found[index] = row
        if verbose:
            print(f"    {index:2}  {row.name[:34]:34} x{row.qty:<4} "
                  f"{row.price:>14,}")
    model.seed(found, top=row_model.MAX_TOP)
    if verbose:
        print(f"  seeded {len(found)} of rows 1-{row_model.MAX_TOP}")
        print(f"  rows {row_model.MAX_TOP + 1}-{CAPACITY} are NOT reachable at "
              f"position 1 and were not read")
    return model


def _row_from(text):
    found = _ROW.match((text or "").strip())
    if found is None:
        return None
    return row_model.Row(
        found.group("name").strip(" |-)("),
        qty=int(found.group("qty").replace(",", "")),
        price=int(found.group("price").replace(",", "")))


def cancel(model, index, verbose=True):
    return model.cancel(index, verbose=verbose)


def list_row(model, index, verbose=True):
    raise NotImplementedError(
        "listing is not built yet. cancel(N) was specified step by step -- "
        "scroll, Change, Cancel, Confirmation -- and listing needs the same: "
        "which button opens the register panel, where the price and quantity "
        "go, and what confirms it. Guessing that flow would commit real money.")


def report(model):
    print(model.report())


def main():
    verbose = True
    cal = initialise(verbose=verbose)
    print(f"  balance {balance() or 'unreadable'}")
    model = seed(verbose=verbose)
    report(model)
    return model


if __name__ == "__main__":
    main()
