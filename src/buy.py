import contextlib
import re
import sys
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
REREADS = _SHARED["detect"]["panel_rereads"]
REREAD_GAP = _SHARED["timing"]["panel_reread_gap"]
ROW_SELECT_X = _SHARED["detect"]["purchase_row_select_x"]
BUY_ROW = 1


_STEPS = []


@contextlib.contextmanager
def step(label):
    started = time.perf_counter()
    try:
        yield
    finally:
        _STEPS.append((label, (time.perf_counter() - started) * 1000))


def steps_reset():
    _STEPS.clear()


def steps_table(title):
    total = sum(ms for _l, ms in _STEPS)
    print("")
    print(f"  {title}")
    print(f"  {'#':>3}  {'ms':>9}  {'share':>6}  step")
    for i, (label, ms) in enumerate(_STEPS, start=1):
        share = (ms / total * 100) if total else 0
        print(f"  {i:>3}  {ms:>9.1f}  {share:>5.1f}%  {label}")
    print(f"       {total:>9.1f}  100.0%  TOTAL")
    return total


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
    with step("get_price: search the favourite and read row 1"):
        offer = get_price.get_price(int(slot), verbose=False)
    if offer is None:
        raise Refused(f"favourite slot {slot} would not price, so there is "
                      f"nothing to buy from.")
    name = calibration.FAVOURITE_ITEMS[str(int(slot))]
    say(f"  row 1 offers {offer['name']!r} x{offer['qty']} at "
        f"{offer['price']:,} ({offer['unit_price']:,}/unit)")

    with step(f"click the row at {row_point()}"):
        calibration.click(*row_point())
    with step(f"sleep(action_gap {ACTION_GAP:g})"):
        time.sleep(ACTION_GAP)
    with step(f"click Buy at {buy_point()}"):
        calibration.click(*buy_point())
    with step("await the Purchase dialog"):
        appeared = await_dialog()
    if not appeared:
        raise Refused(
            f"no {DIALOG_MARKER} dialog appeared after clicking Buy on row 1. "
            f"Nothing was confirmed.")

    with step("read the dialog (item, price, qty, qty_max)"):
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
    if detail["qty"] != asked:
        with step(f"click the quantity field and type {asked}"):
            calibration.click(*calibration._point(
                tuple(calibration._REG["buy_dialog_qty"])[:2]))
            row_model.type_number(asked, CLEAR_PRESSES_QTY)
            calibration.park()
    elif verbose:
        say(f"    the quantity field already reads {asked}; not retyping it")

    again = None
    for attempt in range(1, REREADS + 2):
        with step(f"re-read the dialog ({attempt})"):
            again = dialog_details()
        if again["qty"] == asked and again["price"] == detail["price"]:
            break
        if verbose:
            say(f"    read {attempt}: qty {again['qty']}, price "
                f"{again['price']} -- wanted {asked} at {detail['price']}")
        time.sleep(REREAD_GAP)
    if again["qty"] != asked:
        _cancel(f"the dialog reads {again['qty']} after {REREADS + 1} reads, "
                f"not {asked}. Cancelled without buying.")
    if again["price"] != detail["price"]:
        _cancel(f"the dialog price moved from {detail['price']} to "
                f"{again['price']}. Cancelled without buying.")

    with step(f"find the {CONFIRM_WORD} button"):
        point = dialog_button(CONFIRM_WORD)
    if point is None:
        _cancel(f"no {CONFIRM_WORD} button on the dialog. Cancelled.")
    with step(f"click {CONFIRM_WORD}"):
        calibration.click(*point)
    with step(f"sleep(action_gap {ACTION_GAP:g})"):
        time.sleep(ACTION_GAP)
    with step("park"):
        calibration.park()
    with step("confirm the dialog is gone"):
        still = dialog_open()
    if still:
        raise Refused(
            f"the dialog stayed open after {CONFIRM_WORD}. Whether anything "
            f"was bought is unknown -- look before running again.")
    units = asked * max(1, row_model.pack_size(offer["name"]))
    say(f"    bought {asked} x {offer['name']} = {units} core(s) for "
        f"{asked * offer['price']:,}")
    return {"slot": int(slot), "name": name, "packs": asked, "bought": units,
            "unit_price": offer["unit_price"], "price": offer["price"]}


def buy_item(slot, want=None, verbose=True):
    import driver
    driver.initialise(verbose=verbose)
    name = calibration.FAVOURITE_ITEMS[str(int(slot))]
    want = int(want) if want is not None else int(
        _SHARED["resupply"]["buy_min"])
    print(f"buy_item({slot}) {name!r}, wanting {want} core(s)")
    calibration.click(*calibration.inventory_tab_point(
        calibration.CONVERT_INVENTORY_TAB))
    time.sleep(TAB_SETTLE)

    steps_reset()
    started = time.perf_counter()
    try:
        out = buy_row_one(int(slot), want, verbose=verbose)
    except Refused as exc:
        steps_table(f"buy_item({slot}) REFUSED after "
                    f"{(time.perf_counter() - started) * 1000:.0f} ms")
        print(f"  refused: {exc}")
        return None
    steps_table(f"buy_item({slot}) bought {out['bought']} core(s) in "
                f"{out['packs']} order(s)")
    return out


def main():
    calibration.log_to_file("buy")
    args = [a for a in sys.argv[1:] if a != "--frames"]
    calibration.frames_on(True if "--frames" in sys.argv[1:] else None)
    if not args:
        print("usage:")
        print("  py src/buy.py N [WANT]   buy from favourite slot N, timing")
        print("                           every step; WANT defaults to")
        print("                           resupply.buy_min in config.json")
        for slot in sorted(calibration.FAVOURITE_ITEMS, key=int):
            print(f"      {slot:>2}  {calibration.FAVOURITE_ITEMS[slot]}")
        sys.exit(2)
    buy_item(int(args[0]), int(args[1]) if len(args) > 1 else None)


if __name__ == "__main__":
    main()
