import re
import time

import calibration

_SHARED = calibration.load_shared()
_TEXT = _SHARED["text"]
_REG = _SHARED["regions"]
_T = _SHARED["timing"]
_DET = _SHARED["detect"]
CASH = _SHARED["resupply"]["cash_shop"]
DEFAULT_TAB = str(CASH["tabs"]["default"])
PURCHASE_WORD = _TEXT["cash_purchase_word"]
OK_WORD = _TEXT["cash_ok_word"]
CANCEL_WORD = _TEXT["cash_cancel_word"]
PRICE_WORD = _TEXT["cash_price_word"]
BALANCE_WORD = _TEXT["cash_balance_word"]
ICON_F = tuple(_REG["cash_icon"])
TABS_F = tuple(_REG["cash_tabs"])
TITLE_F = tuple(_REG["cash_title"])
TITLE_WORD = _TEXT["cash_title_word"]
GRID_F = tuple(_REG["cash_grid"])
DIALOG_F = tuple(_REG["cash_dialog"])
BALANCE_F = tuple(_REG["cash_balance"])
CELL_REACH_F = tuple(_DET["cash_cell_reach"])
REREADS = _DET["panel_rereads"]
REREAD_GAP = _T["panel_reread_gap"]
ACTION_GAP = _T["action_gap"]
TAB_SETTLE = _T["tab_settle"]
POLL_GAP = _T["poll_gap"]
DIALOG_TIMEOUT = _T["dialog_timeout"]
TOGGLE_TRIES = int(_T["toggle_tries"])
LINE_SLACK = _DET["word_row_slack"]
BUTTON_HALF = tuple(_DET["dialog_button_half"])


class Refused(Exception):
    pass


def _fold(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def items():
    return [str(name) for name in CASH["rows"]]


def rows_wanted(item):
    return int(calibration._per_item_raw(CASH["rows"], item) or 0)


def tab_for(item):
    return str(calibration._per_item_raw(CASH["tabs"], item) or DEFAULT_TAB)


def matches(item, text):
    named = calibration.voucher_floor_ratio(text)[0]
    if named is not None:
        return _fold(named) == _fold(item)
    return _fold(item) in _fold(text)


def _cal():
    return calibration.load().get("cashshop") or {}


def _ocr(box, image=None):
    image = image if image is not None else calibration.grab()
    return calibration.ocr(image, box)


def _band(frac, image=None):
    return _ocr(calibration._box(frac), image)


def _find(words, word):
    return next((p for t, _c, p in words if _fold(t) == _fold(word)), None)


def _text(words):
    return " ".join(t for t, _c, _p in sorted(
        words, key=lambda w: (w[2][1] // LINE_SLACK, w[2][0])))


def _figure_after(words, word):
    anchor = _find(words, word)
    if anchor is None:
        return None
    for _x, text in sorted((p[0], t) for t, _c, p in words
                           if abs(p[1] - anchor[1]) <= LINE_SLACK
                           and p[0] > anchor[0]):
        digits = re.sub(r"[^0-9]", "", text)
        if digits:
            return int(digits)
    return None


def seat_box(point):
    dx, dy = BUTTON_HALF
    return (point[0] - dx, point[1] - dy, point[0] + dx, point[1] + dy)


def _reads_at(word, point, image=None):
    return _find(_ocr(seat_box(point), image), word) is not None


def seat(name, word, frac, image=None):
    image = image if image is not None else calibration.grab()
    known = _cal().get(name)
    if known and _reads_at(word, tuple(known), image):
        return tuple(known)
    point = _find(_band(frac, image), word)
    if point is not None and list(point) != known:
        calibration.remember("cashshop", {name: list(point)})
        print(f"    learned {word} at {list(point)}; it will be looked for "
              f"there from now on")
    return point


def icon_point():
    known = _cal().get("icon")
    return tuple(known) if known else calibration._point(ICON_F)


def window_box():
    known = _cal().get("window")
    return tuple(known) if known else calibration._box(DIALOG_F)


def balance_box():
    known = _cal().get("balance")
    return tuple(known) if known else calibration._box(BALANCE_F)


def read_cc(image=None):
    image = image if image is not None else calibration.grab()
    return calibration.read_money(image, balance_box())


def cc_now(verbose=True):
    say = print if verbose else (lambda *a: None)
    value = None
    for attempt in range(1, REREADS + 2):
        value = read_cc()
        if value is not None:
            break
        time.sleep(REREAD_GAP)
    if value is None:
        calibration.snap("cash_balance_unread")
        say(f"  the Cash balance box {list(balance_box())} would not read "
            f"in {REREADS + 1} read(s)")
    else:
        say(f"  the Cash balance box {list(balance_box())} reads {value:,}")
    return value


def get_cc(verbose=True):
    opened = False
    if not is_open():
        open_cash_shop(verbose=verbose)
        opened = True
    value = cc_now(verbose=verbose)
    if opened:
        close_cash_shop(verbose=verbose)
    return value


def tab_point(word=None, image=None):
    word = word or DEFAULT_TAB
    return seat("tab_" + _fold(word), word, TABS_F, image)


def title_shown(image=None):
    image = image if image is not None else calibration.grab()
    seen = calibration.read_line(image, calibration._box(TITLE_F))
    return _fold(TITLE_WORD) in _fold(seen)


def is_open(image=None):
    image = image if image is not None else calibration.grab()
    return tab_point(DEFAULT_TAB, image) is not None or title_shown(image)


def cell_box(purchase):
    _x, _y, w, h = calibration._client_rect()
    left, up, right, down = CELL_REACH_F
    return (purchase[0] - round(left * w), purchase[1] - round(up * h),
            purchase[0] + round(right * w), purchase[1] + round(down * h))


def find_cell(item, image=None):
    image = image if image is not None else calibration.grab()
    key = "purchase_" + _fold(item)
    known = _cal().get(key)
    words = _band(GRID_F, image)
    buys = [p for t, _c, p in words if _fold(t) == _fold(PURCHASE_WORD)]
    if known:
        buys.sort(key=lambda p: list(p) != known)
    for buy in buys:
        box = cell_box(buy)
        cell = [(t, p) for t, _c, p in words
                if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]]
        text = " ".join(t for t, p in sorted(
            cell, key=lambda w: (w[1][1] // LINE_SLACK, w[1][0])))
        if not matches(item, text):
            continue
        price = None
        for t, p in sorted(cell, key=lambda w: w[1][0]):
            if abs(p[1] - buy[1]) <= LINE_SLACK and p[0] < buy[0]:
                digits = re.sub(r"[^0-9]", "", t)
                if digits:
                    price = int(digits)
        if list(buy) != known:
            calibration.remember("cashshop", {key: list(buy)})
            print(f"    learned {PURCHASE_WORD} for {item!r} at {list(buy)}; "
                  f"it will be looked for there from now on")
        return {"item": item, "text": text, "purchase": tuple(buy),
                "price": price}
    return None


def dialog(image=None):
    image = image if image is not None else calibration.grab()
    ok = seat("ok", OK_WORD, DIALOG_F, image)
    cancel = seat("cancel", CANCEL_WORD, DIALOG_F, image)
    if ok is None or cancel is None:
        return None
    words = _ocr(window_box(), image)
    return {"ok": ok, "cancel": cancel, "text": _text(words),
            "price": _figure_after(words, PRICE_WORD),
            "balance": _figure_after(words, BALANCE_WORD)}


def _await(read, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        seen = read()
        if seen:
            return seen
        time.sleep(POLL_GAP)
    return None


def _await_gone(read, timeout=None):
    deadline = time.monotonic() + (DIALOG_TIMEOUT if timeout is None
                                   else timeout)
    while time.monotonic() < deadline:
        if not read():
            return True
        time.sleep(POLL_GAP)
    return False


def open_cash_shop(verbose=True):
    say = print if verbose else (lambda *a: None)
    if calibration._trade_window_open():
        raise Refused("the Agent Shop is open and the Cash Shop is not "
                      "opened over it. Nothing clicked.")
    if is_open():
        if tab_point(DEFAULT_TAB) is not None:
            say("  the Cash Shop is already open")
            return True
        say(f"  the Cash Shop is open on another view, without its "
            f"{DEFAULT_TAB} tab; closing it and opening it afresh")
        close_cash_shop(verbose=verbose)
    icon = icon_point()
    say(f"  the Cash Shop icon at {list(icon)}")
    calibration.click(*icon)
    if _await(is_open) is None:
        calibration.snap("cash_shop_never_opened")
        raise Refused(
            f"no {DEFAULT_TAB!r} tab within {DIALOG_TIMEOUT:g}s of clicking "
            f"the Cash Shop icon at {list(icon)}; the Cash Shop is not open "
            f"where it was expected.")
    return True


def select_tab(word=None, verbose=True):
    say = print if verbose else (lambda *a: None)
    word = word or DEFAULT_TAB
    point = tab_point(word)
    if point is None:
        raise Refused(f"no {word!r} tab on the Cash Shop; nothing clicked.")
    say(f"  {word} at {list(point)}")
    calibration.click(*point)
    time.sleep(TAB_SETTLE)
    calibration.park()
    return point


def cell_for(item, verbose=True):
    say = print if verbose else (lambda *a: None)
    cell = _await(lambda: find_cell(item))
    if cell is None:
        calibration.snap("cash_cell_not_found_" + _fold(item))
        raise Refused(
            f"{item!r} is not on the {tab_for(item)} tab's first page with a "
            f"{PURCHASE_WORD} button; nothing clicked.")
    if cell["price"] is None:
        calibration.snap("cash_cell_no_price_" + _fold(item))
        raise Refused(f"{item!r} was found but its price would not read; "
                      f"not clicking {PURCHASE_WORD}.")
    say(f"  {item!r} at {cell['price']} Cash; {PURCHASE_WORD} at "
        f"{list(cell['purchase'])}")
    return cell


def purchase_cell(item, verbose=True):
    cell = cell_for(item, verbose=verbose)
    calibration.click(*cell["purchase"], settle=0.0)
    return cell


def _dismiss(seen, why):
    calibration.click(*seen["cancel"])
    calibration.park()
    _await_gone(dialog)
    raise Refused(why)


def close_cash_shop(verbose=True):
    from open_inventory import VK_ESCAPE, press
    say = print if verbose else (lambda *a: None)
    for attempt in range(1, TOGGLE_TRIES + 1):
        if not is_open():
            return True
        say(f"  Escape to close the Cash Shop (attempt {attempt})")
        press(VK_ESCAPE)
        time.sleep(ACTION_GAP)
        if _await_gone(is_open):
            calibration.park()
            return True
    calibration.snap("cash_shop_stays_open")
    raise Refused("the Cash Shop stayed open after two Escapes.")


def purchase(item, confirm=True, verbose=True):
    say = print if verbose else (lambda *a: None)
    calibration.steps_reset()
    with calibration.step(f"{PURCHASE_WORD} on {item}"):
        purchase_cell(item, verbose=verbose)
    with calibration.step("await the confirmation"):
        seen = _await(dialog)
    if seen is None:
        calibration.snap("cash_no_confirmation")
        raise Refused(
            f"no {OK_WORD} and {CANCEL_WORD} within {DIALOG_TIMEOUT:g}s of "
            f"{PURCHASE_WORD}. Nothing confirmed.")
    say(f"    the confirmation reads {seen['text']!r}")
    if not matches(item, seen["text"]):
        _dismiss(seen, f"the confirmation names something other than "
                       f"{item!r}. Cancelled without buying.")
    if seen["price"] is None or seen["balance"] is None:
        _dismiss(seen, f"the confirmation's {PRICE_WORD} or {BALANCE_WORD} "
                       f"would not read. Cancelled without buying.")
    say(f"    {item} costs {seen['price']} Cash; the balance is "
        f"{seen['balance']} Cash")
    if seen["balance"] < seen["price"]:
        _dismiss(seen, f"{seen['balance']} Cash held and it costs "
                       f"{seen['price']}. Cancelled without buying.")
    word = OK_WORD if confirm else CANCEL_WORD
    with calibration.step(f"click {word}"):
        calibration.click(*seen["ok" if confirm else "cancel"], settle=0.0)
    with calibration.step("confirm the dialog is gone"):
        gone = _await_gone(dialog)
    calibration.park()
    if not gone:
        calibration.snap("cash_confirmation_stays")
        raise Refused(
            f"the confirmation stayed open after {word}. Whether anything "
            f"was bought is unknown; look before running again.")
    outcome = f"bought 1 {item}" if confirm else "cancelled at the confirmation"
    calibration.steps_table(f"Cash Shop: {outcome}")
    return {"item": item, "bought": 1 if confirm else 0,
            "price": seen["price"], "balance": seen["balance"],
            "cancelled": not confirm}


def buy(item, confirm=True, verbose=True):
    open_cash_shop(verbose=verbose)
    select_tab(tab_for(item), verbose=verbose)
    out = purchase(item, confirm=confirm, verbose=verbose)
    close_cash_shop(verbose=verbose)
    return out
