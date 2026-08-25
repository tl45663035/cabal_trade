import re
import sys
import time

import calibration
import ledger
import get_alz
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
FIELD_SETTLE = _SHARED["timing"]["field_settle"]
REREADS = _SHARED["detect"]["panel_rereads"]
REREAD_GAP = _SHARED["timing"]["panel_reread_gap"]
ROW_SELECT_X = _SHARED["detect"]["purchase_row_select_x"]
BUY_ROW = 1


step = calibration.step
steps_reset = calibration.steps_reset
steps_table = calibration.steps_table
_STEPS = calibration._STEPS


class Refused(Exception):
    def __init__(self, message, retryable=False):
        super().__init__(message)
        self.retryable = retryable


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


def _fold(text):
    return re.sub(r"[^a-z]", "", (text or "").lower())


def _button_key(word):
    return f"buy_button_{_fold(word)}"


def remembered(word):
    point = _shop_cal().get(_button_key(word))
    return tuple(point) if point else None


def button_here(word, point, image=None):
    image = image if image is not None else calibration.grab()
    dx, dy = row_model.BUTTON_HALF
    box = (point[0] - dx, point[1] - dy, point[0] + dx, point[1] + dy)
    want = _fold(word)
    return any(_fold(t) == want for t, _c, _p in calibration.ocr(image, box))


def dialog_open(image=None):
    known = remembered(CONFIRM_WORD)
    if known is not None:
        return button_here(CONFIRM_WORD, known, image)
    image = image if image is not None else calibration.grab()
    words = calibration.ocr(image, _reg("buy_dialog"))
    text = " ".join(t for t, _c, _p in words).lower()
    return DIALOG_MARKER.lower() in text


def dialog_button(word, image=None):
    known = remembered(word)
    if known is not None and button_here(word, known, image):
        return known
    image = image if image is not None else calibration.grab()
    want = _fold(word)
    for text, _c, point in calibration.ocr(image, _reg("buy_dialog_buttons")):
        if _fold(text) == want:
            if known is None:
                calibration.remember_shop(_button_key(word), list(point))
                print(f"    learned the {word} button at {point}; it will be "
                      f"looked for there from now on")
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


def _shows(image, box, want):
    first = calibration.read_money(image, box)
    if first == want:
        return True, [first]
    every = calibration.read_money_all(image, box)
    return want in every, every


def dialog_holds(image, per_pack, most):
    image = image if image is not None else calibration.grab()
    quantities = calibration.read_money_all(image, _reg("buy_dialog_qty"))
    totals = calibration.read_money_all(image, _reg("buy_dialog_price"))
    for qty in sorted((q for q in quantities if q), reverse=True):
        if 1 <= qty <= most and per_pack * qty in totals:
            return qty, per_pack * qty, quantities, totals
    return None, None, quantities, totals


def _cancel(why, retryable=False):
    calibration.snap("buy_cancelled")
    point = dialog_button(CANCEL_WORD)
    if point is not None:
        calibration.click(*point)
        time.sleep(ACTION_GAP)
    if dialog_open():
        from open_inventory import press
        press(_SHARED["input"]["VK_ESCAPE"])
        time.sleep(ACTION_GAP)
    calibration.park()
    raise Refused(why, retryable=retryable)


def await_balance(differs_from=None, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    seen = None
    while time.monotonic() < deadline:
        seen = get_alz.read_balance()
        if seen is not None and seen != differs_from:
            return seen
        time.sleep(POLL_GAP)
    return seen


def buy_row_one(slot, want, verbose=True, held=0, floor_qty=0,
                ceiling=None, sells_at=0, gap=None):
    steps_reset()
    outcome = "REFUSED"
    try:
        out = _buy_row_one(slot, want, verbose=verbose, held=held,
                           floor_qty=floor_qty, ceiling=ceiling,
                           sells_at=sells_at, gap=gap)
        outcome = f"bought {out['bought']} core(s) in {out['packs']} order(s)"
        return out
    finally:
        if verbose and _STEPS:
            steps_table(f"buy from favourite slot {slot}: {outcome}")


def _buy_row_one(slot, want, verbose=True, held=0, floor_qty=0,
                 ceiling=None, sells_at=0, gap=None):
    say = print if verbose else (lambda *a: None)
    with step("get_price: search the favourite and read row 1"):
        offer = get_price.get_price(int(slot), verbose=False)
    if offer is None:
        calibration.snap(f"buy_slot_{slot}_would_not_price")
        raise Refused(f"favourite slot {slot} would not price, so there is "
                      f"nothing to buy from.")
    name = calibration.FAVOURITE_ITEMS[str(int(slot))]
    say(f"  row 1 offers {offer['name']!r} x{offer['qty']} at "
        f"{offer['price']:,} ({offer['unit_price']:,}/unit)")
    if sells_at and gap is not None:
        now = sells_at - offer["unit_price"]
        if now <= gap:
            calibration.snap("buy_gap_too_thin")
            raise Refused(
                f"row 1 asks {offer['unit_price']:,} and the core sells at "
                f"{sells_at:,}, a gap of {now:,} against the {gap:,} wanted. "
                f"Nothing bought.")
        say(f"    row 1 leaves {now:,} a core against the {gap:,} wanted")

    with step(f"click the row at {row_point()}"):
        calibration.click(*row_point(), settle=FIELD_SETTLE)
    with step(f"click Buy at {buy_point()}"):
        calibration.click(*buy_point(), settle=0.0)
    with step("await the Purchase dialog"):
        appeared = await_dialog()
    if not appeared:
        calibration.snap("buy_no_dialog_after_buy")
        raise Refused(
            f"no {DIALOG_MARKER} dialog appeared after clicking Buy on row 1. "
            f"Nothing was confirmed.", retryable=True)

    with step("read the dialog (item, price, qty, qty_max)"):
        detail = dialog_details()
    say(f"    dialog: {detail['item']!r}  qty {detail['qty']} of "
        f"{detail['qty_max']}  price {detail['price']}")
    fold = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    if fold(name) not in fold(detail["item"]):
        _cancel(f"the dialog offers {detail['item']!r}, not {name!r}. "
                f"Cancelled without buying.", retryable=True)
    if not detail["qty_max"]:
        _cancel(f"the dialog offers a maximum of {detail['qty_max']}. "
                f"Cancelled without buying.")

    pack = max(1, row_model.pack_size(offer["name"]))
    want_packs = max(1, -(-int(want) // pack))
    asked = min(want_packs, int(detail["qty_max"]))
    if ceiling is not None and held + pack * asked > ceiling:
        if held <= 0:
            say(f"    nothing held yet: taking row 1's bundle of {pack} even "
                f"though {held + pack * asked} passes the {ceiling} ceiling "
                f"-- a bundle cannot be split and buying is row 1 only")
        else:
            fits = max(0, ceiling - held) // pack
            if fits < 1:
                _cancel(f"{held} already held and row 1 bundles {pack}, so "
                        f"even one would pass the {ceiling} ceiling. "
                        f"Cancelled without buying.")
            say(f"    {held} held; trimming this order from {asked} to "
                f"{fits} pack(s) to stay under the {ceiling} ceiling")
            asked = fits
    if verbose:
        say(f"    {want} core(s) wanted, {pack} to a pack -> {want_packs} "
            f"pack(s); {detail['qty_max']} available, taking {asked}")
    if detail["qty"] != asked:
        with step(f"click the quantity field and type {asked}"):
            calibration.click(*calibration._centre(
                tuple(calibration._REG["buy_dialog_qty"])))
            row_model.type_number(asked, CLEAR_PRESSES_QTY)
            calibration.park()
    elif verbose:
        say(f"    the quantity field already reads {asked}; not retyping it")

    per_pack = detail["price"] // max(1, detail["qty"] or 1)
    if per_pack != offer["price"] and verbose:
        say(f"    the row priced {offer['name']!r} at {offer['price']:,} and "
            f"the dialog prices one pack at {per_pack:,}; the dialog is what "
            f"gets paid")
    holds = want_total = None
    seen_qty, seen_price = [], []
    for attempt in range(1, REREADS + 2):
        with step(f"re-read the dialog ({attempt})"):
            holds, want_total, seen_qty, seen_price = dialog_holds(
                None, per_pack, asked)
        if holds:
            break
        if verbose:
            say(f"    read {attempt}: the quantity reads "
                f"{seen_qty or 'nothing'} and the price reads "
                f"{seen_price or 'nothing'} -- neither pair agrees at "
                f"{per_pack:,} a pack")
        time.sleep(REREAD_GAP)
    if not holds:
        _cancel(f"the dialog would not say how many packs it holds after "
                f"{REREADS + 1} reads; the quantity reads "
                f"{seen_qty or 'nothing'} and the price reads "
                f"{seen_price or 'nothing'}, and no pair of them agrees at "
                f"{per_pack:,} a pack. Cancelled without buying.",
                retryable=True)
    if holds != asked and verbose:
        say(f"    the dialog holds {holds} pack(s), not the {asked} typed; "
            f"taking what is there")
    asked = holds
    if verbose:
        say(f"    dialog confirms {asked} pack(s) at {want_total:,}")

    with step("read the balance before buying"):
        before_alz = get_alz.read_balance()
    if before_alz is None:
        _cancel("the Alz balance would not read, so a purchase could not be "
                "checked against it. Cancelled without buying.")
    say(f"    balance before {before_alz:,}")
    if before_alz < want_total:
        _cancel(f"the order costs {want_total:,} and only {before_alz:,} is "
                f"held. Cancelled without buying; this core waits for the "
                f"next cycle.")

    with step(f"find the {CONFIRM_WORD} button"):
        point = dialog_button(CONFIRM_WORD)
    if point is None:
        _cancel(f"no {CONFIRM_WORD} button on the dialog. Cancelled.")
    with step(f"click {CONFIRM_WORD}"):
        calibration.click(*point, settle=0.0)
    with step("park"):
        calibration.park()
    with step("confirm the dialog is gone"):
        still = dialog_open()
    if still:
        calibration.snap("buy_dialog_stayed_open")
        raise Refused(
            f"the dialog stayed open after {CONFIRM_WORD}. Whether anything "
            f"was bought is unknown -- look before running again.")
    with step("read the balance after buying"):
        after_alz = await_balance(differs_from=before_alz)
    units = asked * max(1, row_model.pack_size(offer["name"]))
    if after_alz is None:
        calibration.snap("buy_balance_unread_after_confirm")
        raise Refused(
            f"the Alz balance would not read after {CONFIRM_WORD}. Whether "
            f"{want_total:,} was spent is unknown -- check by hand.")
    spent = before_alz - after_alz
    for attempt in range(1, REREADS + 1):
        if spent == want_total:
            break
        say(f"    balance read {attempt}: {after_alz:,} makes the spend "
            f"{spent:,}, and the dialog priced it at {want_total:,}; "
            f"reading again")
        time.sleep(REREAD_GAP)
        again = get_alz.read_balance()
        if again is None:
            continue
        after_alz, spent = again, before_alz - again
    per_unit = spent // units if units else 0
    say(f"    balance after  {after_alz:,}; spent {spent:,} "
        f"({per_unit:,} a core)")
    if spent != want_total:
        calibration.snap("buy_spend_disagrees")
        raise Refused(
            f"{spent:,} left the account for an order the dialog priced at "
            f"{want_total:,} after {REREADS} reads. Balance {before_alz:,} -> "
            f"{after_alz:,}. The pack was bought; the Sets are in the bag.")
    say(f"    bought {asked} x {offer['name']} = {units} core(s) for "
        f"{want_total:,}")
    ledger.bought(offer["name"], per_unit, spent, units)
    return {"slot": int(slot), "name": name, "packs": asked, "bought": units,
            "unit_price": per_unit, "price": offer["price"],
            "spent": spent, "balance": after_alz}


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

    try:
        return buy_row_one(int(slot), want, verbose=verbose)
    except Refused as exc:
        print(f"  refused: {exc}")
        return None


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
