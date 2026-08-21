import re
import time

import calibration
import get_price
import row_model
import open_agent_shop_premium as shop

_SHARED = calibration.load_shared()
_TEXT = _SHARED["text"]
CONFIRM_WORD = _TEXT["buy_confirm_word"]
CANCEL_WORD = _TEXT["buy_cancel_word"]
DIALOG_MARKER = _TEXT["buy_dialog_marker"]
ACTION_GAP = _SHARED["timing"]["action_gap"]
TAB_SETTLE = _SHARED["timing"]["tab_settle"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
DIALOG_TIMEOUT = _SHARED["timing"]["dialog_timeout"]
CLEAR_PRESSES_QTY = _SHARED["detect"]["clear_presses_qty"]
ROW_SELECT_X = _SHARED["detect"]["purchase_row_select_x"]
BUY_ROW = 1


class Refused(Exception):
    pass


def _reg(name):
    return calibration._box(tuple(calibration._REG[name]))


def _shop_cal():
    return calibration.load()["shop"]


def row_point(index=BUY_ROW):
    cal = _shop_cal()
    y = cal["purchase_row_one_y"] + (index - 1) * cal["purchase_row_pitch"]
    return ROW_SELECT_X, y


def buy_point(index=BUY_ROW):
    cal = _shop_cal()
    y = cal["purchase_row_one_y"] + (index - 1) * cal["purchase_row_pitch"]
    return cal["purchase_buy_x"], y


def dialog_open(image=None):
    image = image if image is not None else calibration.grab()
    words = calibration.ocr(image, _reg("buy_dialog"))
    text = " ".join(t for t, _c, _p in words).lower()
    return DIALOG_MARKER.lower() in text


def dialog_button(word, image=None):
    image = image if image is not None else calibration.grab()
    want = re.sub(r"[^a-z]", "", word.lower())
    for text, _c, point in calibration.ocr(image, _reg("buy_dialog_buttons")):
        if re.sub(r"[^a-z]", "", text.lower()) == want:
            return point
    return None


def await_dialog(timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        if dialog_open():
            return True
        time.sleep(POLL_GAP)
    return False


def dialog_details(image=None):
    image = image if image is not None else calibration.grab()
    return {"item": calibration.read_line(image, _reg("buy_dialog_item")),
            "price": calibration.read_money(image, _reg("buy_dialog_price")),
            "qty": calibration.read_money(image, _reg("buy_dialog_qty")),
            "qty_max": calibration.read_money(image,
                                              _reg("buy_dialog_qty_max"))}


def _cancel(why):
    point = dialog_button(CANCEL_WORD)
    if point is not None:
        calibration.click(*point)
        time.sleep(ACTION_GAP)
    if dialog_open():
        from open_inventory import press
        press(_SHARED["input"]["VK_ESCAPE"])
        time.sleep(ACTION_GAP)
    calibration.park()
    raise Refused(why)


def buy_row_one(slot, want, verbose=True):
    say = print if verbose else (lambda *a: None)
    offer = get_price.get_price(int(slot), verbose=False)
    if offer is None:
        raise Refused(f"favourite slot {slot} would not price, so there is "
                      f"nothing to buy from.")
    name = calibration.FAVOURITE_ITEMS[str(int(slot))]
    say(f"  row 1 offers {offer['name']!r} x{offer['qty']} at "
        f"{offer['price']:,} ({offer['unit_price']:,}/unit)")

    calibration.click(*row_point())
    time.sleep(ACTION_GAP)
    calibration.click(*buy_point())
    if not await_dialog():
        raise Refused(
            f"no {DIALOG_MARKER} dialog appeared after clicking Buy on row 1. "
            f"Nothing was confirmed.")

    detail = dialog_details()
    say(f"    dialog: {detail['item']!r}  qty {detail['qty']} of "
        f"{detail['qty_max']}  price {detail['price']}")
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    if fold(name) not in fold(detail["item"]):
        _cancel(f"the dialog offers {detail['item']!r}, not {name!r}. "
                f"Cancelled without buying.")
    if not detail["qty_max"]:
        _cancel(f"the dialog offers a maximum of {detail['qty_max']}. "
                f"Cancelled without buying.")

    asked = min(int(want), int(detail["qty_max"]))
    calibration.click(*calibration._point(
        tuple(calibration._REG["buy_dialog_qty"])[:2]))
    row_model.type_number(asked, CLEAR_PRESSES_QTY)
    calibration.park()

    again = dialog_details()
    if again["qty"] != asked:
        _cancel(f"the dialog reads {again['qty']} after typing {asked}. "
                f"Cancelled without buying.")
    if again["price"] != detail["price"]:
        _cancel(f"the dialog price moved from {detail['price']} to "
                f"{again['price']} while typing. Cancelled without buying.")

    point = dialog_button(CONFIRM_WORD)
    if point is None:
        _cancel(f"no {CONFIRM_WORD} button on the dialog. Cancelled.")
    calibration.click(*point)
    time.sleep(ACTION_GAP)
    calibration.park()
    if dialog_open():
        raise Refused(
            f"the dialog stayed open after {CONFIRM_WORD}. Whether anything "
            f"was bought is unknown -- look before running again.")
    say(f"    bought {asked} {name}")
    return {"slot": int(slot), "name": name, "bought": asked,
            "unit_price": offer["unit_price"], "price": offer["price"]}
