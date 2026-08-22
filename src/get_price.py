import re
import sys
import time

import calibration
import open_agent_shop_premium as shop
import open_inventory as inv
import row_model

_SHARED = calibration.load_shared()
ACTION_GAP = _SHARED["timing"]["action_gap"]
POLL_GAP = _SHARED["timing"]["poll_gap"]
TAB_SETTLE = _SHARED["timing"]["tab_settle"]
SEARCH_TIMEOUT = _SHARED["timing"]["search_timeout"]
RETRY_GAP = _SHARED["timing"]["retry_gap"]
RETRIES = _SHARED["timing"]["search_retries"]
EXPECTED = _SHARED["favourite_items"]
_DET = _SHARED["detect"]
BULK_MIN_CONF = _DET["bulk_min_conf"]
RESCUE_MIN_CONF = _DET["rescue_min_conf"]
MIN_PLAUSIBLE_PRICE = _DET["min_plausible_price"]
PRICE_MIN_DIGITS = _DET["price_min_digits"]
SHOP_CHECK_GAP = _SHARED["timing"]["shop_check_gap"]

_NUMBER = re.compile(r"\d[\d,]*")
_NOT_DIGIT = re.compile(r"[^0-9]")
_ROW = re.compile(_SHARED["text"]["purchase_row"])
_SORT_DIRECTION = re.compile(_SHARED["text"]["sort_direction"],
                             re.IGNORECASE)


class NotReady(Exception):
    pass


def _shop_cal():
    return calibration.load()["shop"]


def _need(name):
    value = _shop_cal().get(name)
    if not value:
        raise NotReady(
            f"shop.{name} is not in calibration.json. Re-run "
            f"py src/calibration.py once it measures the Purchase tab.")
    return value


def favourite_point(slot):
    points = _need("favourites")
    slot = int(slot)
    if not 1 <= slot <= len(points):
        raise ValueError(f"favourite slot {slot} is outside 1..{len(points)}")
    return tuple(points[slot - 1])


def sort_box():
    return tuple(_need("purchase_sort_region"))


def purchase_row_one_box():
    return tuple(_need("purchase_row_content"))


def column_box(field):
    cols = _need("purchase_columns")
    if field not in cols:
        raise NotReady(f"shop.purchase_columns has no {field!r} box.")
    return tuple(cols[field])


def read_field(field, image=None):
    image = image if image is not None else calibration.grab()
    return calibration.read_line(image, column_box(field)).strip()


def row_name(image=None):
    return (read_fields(image).get("name") or "").strip()


def column_edges():
    cols = _need("purchase_columns")
    return (cols["qty"][0], cols["price"][0], cols["function"][0])


def read_fields(image=None):
    image = image if image is not None else calibration.grab()
    band = purchase_row_one_box()
    qty_lo, price_lo, function_lo = column_edges()
    tokens = calibration.ocr(image, band, min_conf=BULK_MIN_CONF)

    name_words, qty_words, price_words = [], [], []
    for text, _, (x, _y) in sorted(tokens, key=lambda t: t[2][0]):
        if x < qty_lo:
            name_words.append(text)
        elif x < price_lo:
            qty_words.append(text)
        elif x < function_lo:
            price_words.append(text)

    price = None
    widest = 0
    joined_price = _NOT_DIGIT.sub("", "".join(price_words))
    for text in price_words:
        digits = _NOT_DIGIT.sub("", text)
        if len(digits) >= PRICE_MIN_DIGITS:
            price, widest = int(digits), len(digits)
    if len(joined_price) >= PRICE_MIN_DIGITS and len(joined_price) > widest:
        price = int(joined_price)
    if price is None or price < MIN_PLAUSIBLE_PRICE:
        price = calibration.read_money(image, column_box("price"))

    qty = None
    joined = _NOT_DIGIT.sub("", "".join(qty_words))
    if joined:
        qty = int(joined)
    if qty is None:
        qty = calibration.read_number(image, tuple(_need("purchase_columns")["qty"]))
    if qty is None:
        rescue = calibration.ocr(image,
                                 tuple(_need("purchase_columns")["qty"]),
                                 min_conf=RESCUE_MIN_CONF)
        digits = _NOT_DIGIT.sub("", "".join(t for t, _, _ in rescue))
        qty = int(digits) if digits else None

    return {
        "name": " ".join(name_words).strip(),
        "qty": qty,
        "price": price,
        "row": " ".join(t for t, _, _ in sorted(tokens, key=lambda t: t[2][0])),
    }


def read_sort(image=None):
    image = image if image is not None else calibration.grab()
    return calibration.read_line(image, sort_box())


def confirm_sort_low_to_high(slot, verbose=True):
    deadline = time.monotonic() + SEARCH_TIMEOUT
    seen, found = "", None
    while time.monotonic() < deadline:
        seen = read_sort()
        found = _SORT_DIRECTION.search(seen)
        if found is not None:
            break
        time.sleep(POLL_GAP)
    if found is not None and found.group(1).lower() == "low":
        if verbose:
            print("  sort confirmed Price: Low to High")
        return "ok"
    if not calibration.purchase_tab_showing():
        calibration.snap(f"slot_{slot}_shop_gone_at_sort")
        if calibration.wait_out_server_lag(verbose=verbose):
            return "lagged"
        return "gone"
    calibration.snap(f"slot_{slot}_sort_wrong")
    raise NotReady(f"the sort reads {seen!r}, not Price: Low to High.")


def _digits(text):
    found = _NUMBER.search(text or "")
    return int(found.group(0).replace(",", "")) if found else None


def parse_fields(fields):
    name = (fields.get("name") or "").strip(" |-)(")
    qty = fields.get("qty")
    price = fields.get("price")
    if not name or price is None or price < MIN_PLAUSIBLE_PRICE:
        return None
    if not qty or qty < 1:
        qty = 1
    pack = row_model._PACK.search(name)
    pack = int(pack.group(1)) if pack else 1
    units = max(1, qty * pack)
    total = price * qty
    return {
        "name": name,
        "qty": qty,
        "pack": pack,
        "units": units,
        "price": price,
        "total": total,
        "unit_price": total // units,
    }


def expected_item(slot):
    return EXPECTED.get(str(int(slot)))


def read_row_one(image=None):
    image = image if image is not None else calibration.grab()
    return calibration.read_line(image, purchase_row_one_box())


def name_matches(slot, text):
    want = expected_item(slot)
    if not want:
        return True
    fold = lambda v: "".join(ch for ch in (v or "").lower() if ch.isalnum())
    return fold(want) in fold(text)


def reopen_shop(slot, verbose=True):
    if verbose:
        print(f"  slot {slot}: the Purchase tab is not on screen; "
              f"reopening the Agent Shop")
    calibration.snap(f"slot_{slot}_shop_gone")
    if not calibration._trade_window_open():
        shop.open_agent_shop(verbose=False)
        time.sleep(TAB_SETTLE)
    shop.click(*_need("purchase_tab"))
    time.sleep(TAB_SETTLE)


def get_price(slot, verbose=True):
    with calibration.step("get_price: focus the game"):
        inv.focus_game()
    with calibration.step("get_price: _trade_window_open (OCR 1300x190)"):
        shop_up = calibration._trade_window_open()
    if not shop_up:
        if verbose:
            print("  the Trade window is shut; opening the Agent Shop.")
        shop.open_agent_shop(verbose=verbose)
        time.sleep(TAB_SETTLE)
    with calibration.step("get_price: purchase_tab_showing"):
        on_purchase = calibration.purchase_tab_showing()
    if not on_purchase:
        with calibration.step("get_price: click the Purchase tab + settle"):
            shop.click(*_need("purchase_tab"))
            time.sleep(TAB_SETTLE)

    x, y = favourite_point(slot)
    if verbose:
        print(f"  favourite slot {slot} at ({x}, {y})")
    want = expected_item(slot)
    if verbose and want:
        print(f"  expecting {want!r} at row 1")

    text, row = "", None
    for attempt in range(1, RETRIES + 1):
        if not calibration.purchase_tab_showing():
            reopen_shop(slot, verbose=verbose)
        with calibration.step("get_price: read row 1 before the search"):
            before = row_name()
        stale = None if name_matches(slot, before) else before
        with calibration.step(f"get_price: click favourite slot {slot}"):
            shop.click(x, y, settle=0.0)
        gone = False
        deadline = time.monotonic() + SEARCH_TIMEOUT
        next_check = time.monotonic() + SHOP_CHECK_GAP
        polls = 0
        poll_started = time.monotonic()
        while not gone and time.monotonic() < deadline:
            polls += 1
            image = calibration.grab()
            fields = read_fields(image)
            text = (fields.get("name") or "").strip()
            if text == stale or not name_matches(slot, text):
                if time.monotonic() >= next_check:
                    if not calibration.purchase_tab_showing(image):
                        if calibration.wait_out_server_lag(verbose=verbose):
                            deadline = time.monotonic() + SEARCH_TIMEOUT
                            next_check = time.monotonic() + SHOP_CHECK_GAP
                            continue
                        gone = True
                        break
                    next_check = time.monotonic() + SHOP_CHECK_GAP
                continue
            row = parse_fields(fields)
            if row is not None:
                with calibration.step("get_price: confirm the sort"):
                    sort = confirm_sort_low_to_high(
                        slot, verbose=verbose and attempt == 1)
                if sort == "ok":
                    break
                row = None
                gone = sort in ("gone", "lagged")
                break
            time.sleep(POLL_GAP)
        calibration._STEPS.append(
            (f"get_price: poll row 1 until it answers ({polls} read(s))",
             (time.monotonic() - poll_started) * 1000))
        if row is not None:
            break
        if gone:
            print(f"  slot {slot}: attempt {attempt}/{RETRIES} -- the shop "
                  f"closed mid-search; the row band read {text!r}")
        else:
            print(f"  slot {slot}: attempt {attempt}/{RETRIES} timed out "
                  f"after {SEARCH_TIMEOUT:g}s; row 1 reads {text!r}, "
                  f"expected {want!r}")
        time.sleep(RETRY_GAP)

    if row is None:
        if verbose:
            print(f"  row 1 did not parse; name read {text!r}")
        return None
    units = row["qty"] * row["pack"]
    row["slot"] = int(slot)
    row["units"] = units
    row["unit_price"] = row["total"] // max(1, units)
    row["raw"] = text
    if verbose:
        print(f"  {row['name']}  qty {row['qty']}  pack {row['pack']}  "
              f"units {units}  total {row['total']:,}  "
              f"= {row['unit_price']:,}/unit")
    return row


def main():
    if len(sys.argv) < 2:
        print("usage: py src/get_price.py <favourite slot 1-10>")
        sys.exit(2)
    row = get_price(int(sys.argv[1]))
    if row is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
