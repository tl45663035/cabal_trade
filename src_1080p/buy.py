import ctypes
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


class TooThin(Refused):
    pass


def _reg(name):
    return calibration._box(tuple(calibration._REG[name]))


def _shop_cal():
    return calibration.load()["shop"]


def row_point(index=BUY_ROW):
    cal = _shop_cal()
    y = cal["purchase_row_one_y"] + (index - 1) * cal["purchase_row_pitch"]
    return ROW_SELECT_X, y


def scroll_down(notches=1, verbose=False):
    x, y = row_point(BUY_ROW + 1)
    row_model.inv._user32.SetCursorPos(int(x), int(y))
    event = row_model._wheel_event(-1)
    for _ in range(max(1, int(notches))):
        sent = row_model.inv._user32.SendInput(
            1, ctypes.byref(event), ctypes.sizeof(row_model.inv._Input))
        if sent != 1:
            raise Refused(
                f"SendInput sent {sent} of 1 wheel event over the offers "
                f"table at ({x}, {y}).")
        time.sleep(row_model.WHEEL_GAP)
    calibration.park()
    if verbose:
        print(f"  wheeled {notches} notch(es) down the offers, from row "
              f"{BUY_ROW + 1} at ({x}, {y})")


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


def _cancel(why, retryable=False, kind=Refused):
    point = dialog_button(CANCEL_WORD)
    if point is not None:
        calibration.click(*point)
        time.sleep(ACTION_GAP)
    if dialog_open():
        from open_inventory import press
        press(_SHARED["input"]["VK_ESCAPE"])
        time.sleep(ACTION_GAP)
    calibration.park()
    raise kind(why, retryable=retryable)


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
                ceiling=None, sells_at=0, gap=None, leave_behind=0,
                search=True):
    steps_reset()
    outcome = "REFUSED"
    try:
        out = _buy_row_one(slot, want, verbose=verbose, held=held,
                           floor_qty=floor_qty, ceiling=ceiling,
                           sells_at=sells_at, gap=gap,
                           leave_behind=leave_behind, search=search)
        outcome = f"bought {out['bought']} core(s) in {out['packs']} order(s)"
        return out
    finally:
        if verbose and _STEPS:
            steps_table(f"buy from favourite slot {slot}: {outcome}")


def _buy_row_one(slot, want, verbose=True, held=0, floor_qty=0,
                 ceiling=None, sells_at=0, gap=None, leave_behind=0,
                 search=True):
    say = print if verbose else (lambda *a: None)
    with step("get_price: search the favourite and read row 1"):
        offer = get_price.get_price(int(slot), verbose=False,
                                    search=search)
    if offer is None:
        raise Refused(f"favourite slot {slot} would not price, so there is "
                      f"nothing to buy from.")
    name = calibration.FAVOURITE_ITEMS[str(int(slot))]
    say(f"  row 1 offers {offer['name']!r} x{offer['qty']} at "
        f"{offer['price']:,} ({offer['unit_price']:,}/unit)")
    if leave_behind and int(offer["qty"]) - leave_behind < 1:
        raise TooThin(
            f"row 1 holds {offer['qty']} and {leave_behind} stays behind, so "
            f"there is nothing spare to take here.")
    if sells_at and gap is not None:
        now = sells_at - offer["unit_price"]
        if now < gap:
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
    on_offer = int(detail["qty_max"])
    if leave_behind:
        spare = on_offer - leave_behind
        if spare < 1:
            _cancel(f"the dialog offers {on_offer} and {leave_behind} stays "
                    f"behind, so there is nothing spare on row 1. Cancelled "
                    f"without buying.", kind=TooThin)
        say(f"    {on_offer} on offer, {leave_behind} stays behind, so at "
            f"most {spare} comes off row 1")
        on_offer = spare
    asked = min(want_packs, on_offer)
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
    if per_pack != offer["price"]:
        if verbose:
            say(f"    the dialog price read {per_pack:,} against the row's "
                f"{offer['price']:,}; the row is the shelf price the game "
                f"charges, so trusting it -- the spend confirms it after")
        per_pack = offer["price"]
    want_total = per_pack * asked
    if verbose:
        say(f"    ordering {asked} pack(s) at {want_total:,}; the spend will "
            f"confirm what actually bought")

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
        raise Refused(
            f"the dialog stayed open after {CONFIRM_WORD}. Whether anything "
            f"was bought is unknown -- look before running again.")
    with step("read the balance after buying"):
        after_alz = await_balance(differs_from=before_alz)
    if after_alz is None:
        raise Refused(
            f"the Alz balance would not read after {CONFIRM_WORD}. Whether "
            f"{want_total:,} was spent is unknown -- check by hand.")
    pack = max(1, row_model.pack_size(offer["name"]))
    spent = before_alz - after_alz
    if spent == 0:
        with step("read the balance once more before calling it unbought"):
            again = get_alz.read_balance()
        if again is not None and before_alz - again > 0:
            after_alz, spent = again, before_alz - again
            say(f"    the balance had not caught up; it now reads "
                f"{after_alz:,}, so {spent:,} did leave the account")
        else:
            raise Refused(
                f"the balance never moved from {before_alz:,}, on two reads, "
                f"so nothing was bought: row 1 went while the order was being "
                f"placed. Trying the board again.", retryable=True)
    for attempt in range(1, REREADS + 1):
        if spent > 0 and per_pack and spent % per_pack == 0:
            break
        say(f"    balance read {attempt}: {after_alz:,} makes the spend "
            f"{spent:,}, not a whole multiple of the {per_pack:,} pack "
            f"price; reading again")
        time.sleep(REREAD_GAP)
        again = get_alz.read_balance()
        if again is None:
            continue
        after_alz, spent = again, before_alz - again
    if spent < 0:
        raise Refused(
            f"the balance rose from {before_alz:,} to {after_alz:,} across "
            f"the order, which a purchase cannot do. Check by hand.")
    if not per_pack or spent % per_pack != 0:
        shelf = detail["price"] // max(1, detail["qty"] or 1)
        shelf_pack = max(1, row_model.pack_size(detail["item"]))
        if shelf and spent % shelf == 0:
            say(f"    the spend {spent:,} is no whole multiple of the row's "
                f"{per_pack:,}, but it is {spent // shelf} x the dialog's "
                f"{shelf:,}; the board moved under the read, so the dialog "
                f"is what was bought")
            per_pack, pack = shelf, shelf_pack
        else:
            raise Refused(
                f"the spend {spent:,} is not a whole multiple of the "
                f"{per_pack:,} pack price after {REREADS} reads, nor of the "
                f"dialog's {shelf:,}. Balance {before_alz:,} -> "
                f"{after_alz:,}. Something was bought; check by hand.")
    packs = spent // per_pack
    units = packs * pack
    per_unit = spent // units if units else 0
    say(f"    balance after  {after_alz:,}; spent {spent:,} bought {packs} "
        f"pack(s) = {units} core(s) ({per_unit:,} a core)")
    if packs != asked:
        say(f"    note: asked for {asked} pack(s) but the spend shows "
            f"{packs}; booking what the balance proves")
    ledger.bought(offer["name"], per_unit, spent, units, expect=sells_at)
    return {"slot": int(slot), "name": name, "packs": packs, "bought": units,
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
