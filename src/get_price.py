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
SORT_LIST_HEIGHTS = _DET["sort_list_heights"]
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
    joined_price = _NOT_DIGIT.sub("", "".join(price_words))
    for text in price_words:
        digits = _NOT_DIGIT.sub("", text)
        if len(digits) >= PRICE_MIN_DIGITS:
            price = int(digits)
    if price is None and len(joined_price) >= PRICE_MIN_DIGITS:
        price = int(joined_price)

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
    tokens = calibration.ocr(image, sort_box())
    tokens.sort(key=lambda token: token[2][0])
    return " ".join(token[0] for token in tokens)


def sort_is_low_to_high(image=None):
    found = _SORT_DIRECTION.search(read_sort(image))
    return found is not None and found.group(1).lower() == "low"


def sort_option_lines(image=None):
    image = image if image is not None else calibration.grab()
    left, top, right, bottom = sort_box()
    height = bottom - top
    band = (left, bottom, right, min(image.height, bottom + height * SORT_LIST_HEIGHTS))
    lines = {}
    for text, _, (x, y) in calibration.ocr(image, band):
        lines.setdefault(round(y / max(1, height // 2)), []).append((x, y, text))
    out = []
    for words in lines.values():
        words.sort()
        out.append((" ".join(w[2] for w in words),
                    (words[0][0] + words[-1][0]) // 2,
                    sum(w[1] for w in words) // len(words)))
    return out


def set_sort_low_to_high(verbose=True):
    left, top, right, bottom = sort_box()
    calibration.click((left + right) // 2, (top + bottom) // 2)
    time.sleep(TAB_SETTLE)
    for text, x, y in sort_option_lines():
        found = _SORT_DIRECTION.search(text)
        if found is not None and found.group(1).lower() == "low":
            if verbose:
                print(f"  sort reads {read_sort()!r}; clicking {text!r} at ({x}, {y})")
            calibration.click(x, y)
            time.sleep(TAB_SETTLE)
            return sort_is_low_to_high()
    raise NotReady(
        f"the sort list offers no Price: Low to High. It offers "
        f"{[text for text, _, _ in sort_option_lines()]}.")


def ensure_sort_low_to_high(verbose=True):
    if sort_is_low_to_high():
        if verbose:
            print("  sort confirmed Price: Low to High")
        return True
    if set_sort_low_to_high(verbose=verbose):
        if verbose:
            print("  sort set to Price: Low to High")
        return True
    raise NotReady(
        f"the sort does not read Price: Low to High -- it reads "
        f"{read_sort()!r}. Row 1 is only the cheapest under that sort, so "
        f"nothing here may be trusted until it is set.")


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
    ensure_sort_low_to_high(verbose=False)


def get_price(slot, verbose=True):
    inv.focus_game()
    if not calibration._trade_window_open():
        if verbose:
            print("  the Trade window is shut; opening the Agent Shop.")
        shop.open_agent_shop(verbose=verbose)
        time.sleep(TAB_SETTLE)
    if not calibration.purchase_tab_showing():
        shop.click(*_need("purchase_tab"))
        time.sleep(TAB_SETTLE)
    ensure_sort_low_to_high(verbose=verbose)

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
        before = row_name()
        stale = None if name_matches(slot, before) else before
        shop.click(x, y)
        deadline = time.monotonic() + SEARCH_TIMEOUT
        next_check = time.monotonic() + SHOP_CHECK_GAP
        gone = False
        while time.monotonic() < deadline:
            image = calibration.grab()
            text = row_name(image)
            if text == stale or not name_matches(slot, text):
                if time.monotonic() >= next_check:
                    if not calibration.purchase_tab_showing(image):
                        gone = True
                        break
                    next_check = time.monotonic() + SHOP_CHECK_GAP
                continue
            row = parse_fields(read_fields(image))
            if row is not None:
                break
            time.sleep(POLL_GAP)
        if row is not None:
            break
        if gone:
            print(f"  slot {slot}: attempt {attempt}/{RETRIES} -- the shop "
                  f"closed mid-search; the row band read {text[:40]!r}")
        else:
            print(f"  slot {slot}: attempt {attempt}/{RETRIES} timed out "
                  f"after {SEARCH_TIMEOUT:g}s; row 1 reads {text[:40]!r}, "
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
