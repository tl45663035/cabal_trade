import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

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
RESCUE_MIN_CONF = _DET["rescue_min_conf"]
MIN_PLAUSIBLE_PRICE = _DET["min_plausible_price"]
FIELD_SETTLE = _SHARED["timing"]["field_settle"]
VOUCHER_SEARCH = _SHARED["text"]["voucher_search"]
VOUCHER_WORD = _SHARED["text"]["voucher_word"]
SHOP_CHECK_GAP = _SHARED["timing"]["shop_check_gap"]
SEARCH_SETTLE = _SHARED["timing"]["search_settle"]
FAVOURITE_GAP = _SHARED["timing"]["favourite_gap"]
PARALLEL_READS = int(_SHARED["ocr"]["parallel_reads"])

_NUMBER = re.compile(r"\d[\d,]*")
_NOT_DIGIT = re.compile(r"[^0-9]")
_ROW = re.compile(_SHARED["text"]["purchase_row"])
_SORT_DIRECTION = re.compile(_SHARED["text"]["sort_direction"],
                             re.IGNORECASE)
_POOL = ThreadPoolExecutor(max_workers=PARALLEL_READS)
_SEEN = {}


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


def _band(image):
    return image.crop(purchase_row_one_box()).tobytes()


def last_seen():
    return _SEEN.get("before")


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


def row_mark(fields):
    return ((fields.get("name") or "").strip(),
            fields.get("qty"), fields.get("price"))


def read_fields(image=None):
    image = image if image is not None else calibration.grab()
    read = row_model.paddle_row(image, column_box("name"), column_box("qty"),
                                column_box("price"))
    if read is None:
        if "tesseract" not in _SEEN:
            _SEEN["tesseract"] = row_model._reader["failed"]
            print(f"  {_SEEN['tesseract']}; row 1 is read by Tesseract "
                  f"instead")
        return _tesseract_fields(image)
    name, qty, price = read
    digits = _NOT_DIGIT.sub("", qty or "")
    return _fields((name or "").strip(" |"), int(digits) if digits else None,
                   calibration._digits(price))


def _tesseract_fields(image):
    name = _POOL.submit(calibration.read_line, image, column_box("name"))
    qty = _POOL.submit(calibration.read_line, image, column_box("qty"))
    price = _POOL.submit(calibration.read_money, image, column_box("price"))
    name, qty, price = name.result().strip(" |"), qty.result(), price.result()
    digits = _NOT_DIGIT.sub("", qty)
    qty = (int(digits) if digits
           else calibration.read_number(image, column_box("qty")))
    if qty is None:
        rescue = calibration.ocr(image, column_box("qty"),
                                 min_conf=RESCUE_MIN_CONF)
        digits = _NOT_DIGIT.sub("", "".join(t for t, _, _ in rescue))
        qty = int(digits) if digits else None
    return _fields(name, qty, price)


def _fields(name, qty, price):
    return {
        "name": name,
        "qty": qty,
        "price": price,
        "row": " ".join(str(v) for v in (name, qty, price) if v not in (None, "")),
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


SET_WORD = calibration.load_shared()["game_facts"]["set_word"]


def name_matches(slot, text):
    want = expected_item(slot)
    if not want:
        return True
    fold = lambda v: "".join(ch for ch in (v or "").lower() if ch.isalnum())
    wanted, seen = fold(want), fold(text)
    if SET_WORD in seen and SET_WORD not in wanted:
        return False
    return wanted in seen


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


def get_price(slot, verbose=True, search=True, on_purchase=None, before=None):
    with calibration.step("get_price: focus the game"):
        inv.focus_game()
    if not search:
        with calibration.step("get_price: _trade_window_open (OCR 1300x190)"):
            shop_up = calibration._trade_window_open()
        if not shop_up:
            if verbose:
                print("  the Trade window is shut; opening the Agent Shop.")
            shop.open_agent_shop(verbose=verbose)
            time.sleep(TAB_SETTLE)
    if on_purchase is None:
        with calibration.step("get_price: purchase_tab_showing"):
            on_purchase = calibration.purchase_tab_showing()
    clicked = False
    if not on_purchase:
        with calibration.step("get_price: click the Purchase tab + settle"):
            shop.click(*_need("purchase_tab"), settle=0.0 if search else None)
            time.sleep(TAB_SETTLE)
        clicked = True

    x, y = favourite_point(slot)
    if verbose:
        print(f"  favourite slot {slot} at ({x}, {y})")
    want = expected_item(slot)
    if verbose and want:
        print(f"  expecting {want!r} at row 1")

    text, row = "", None
    if not search:
        with calibration.step("get_price: read row 1 where it stands"):
            text = read_row_one()
            row = parse_fields(read_fields())
        if row is not None and not name_matches(slot, row["name"]):
            if verbose:
                print(f"  row 1 reads {row['name']!r}, which is not what "
                      f"slot {slot} sells; not pricing it")
            row = None
    for attempt in range(1, (RETRIES + 1) if search else 0):
        if clicked or attempt > 1:
            with calibration.step("get_price: is the Purchase tab showing"):
                showing = calibration.purchase_tab_showing()
            if not showing:
                reopen_shop(slot, verbose=verbose)
        if attempt == 1 and before is not None:
            was, was_band = before
        else:
            with calibration.step("get_price: read row 1 before the search"):
                image = calibration.grab()
                was, was_band = row_mark(read_fields(image)), _band(image)
        stale = None if name_matches(slot, was[0]) else was[0]
        wait = FAVOURITE_GAP - (time.monotonic() - _SEEN.get("searched", 0.0))
        if wait > 0:
            with calibration.step("get_price: wait out the gap since the last "
                                  "search"):
                time.sleep(wait)
        with calibration.step(f"get_price: click favourite slot {slot}"):
            shop.click(x, y, settle=0.0)
        _SEEN["searched"] = time.monotonic()
        gone = False
        deadline = time.monotonic() + SEARCH_TIMEOUT
        next_check = time.monotonic() + SHOP_CHECK_GAP
        looks = reads = 0
        sort_ms = 0.0
        poll_started = time.monotonic()
        settled = time.monotonic() + SEARCH_SETTLE
        told = False
        while not gone and time.monotonic() < deadline:
            looks += 1
            image = calibration.grab()
            if _band(image) == was_band and time.monotonic() < settled:
                continue
            reads += 1
            sort_seen = _POOL.submit(read_sort, image)
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
            if row_mark(fields) == was and time.monotonic() < settled:
                if verbose and not told:
                    told = True
                    print(f"    row 1 still reads what it did before the "
                          f"search ({was[0]!r} x{was[1]} at {was[2]}); "
                          f"giving the table up to {SEARCH_SETTLE:g}s to come "
                          f"back before trusting it")
                time.sleep(POLL_GAP)
                continue
            row = parse_fields(fields)
            if row is not None:
                sort_started = time.monotonic()
                with calibration.step("get_price: confirm the sort"):
                    found = _SORT_DIRECTION.search(sort_seen.result())
                    if found is not None and found.group(1).lower() == "low":
                        if verbose and attempt == 1:
                            print("  sort confirmed Price: Low to High")
                        sort = "ok"
                    else:
                        sort = confirm_sort_low_to_high(
                            slot, verbose=verbose and attempt == 1)
                sort_ms += (time.monotonic() - sort_started) * 1000
                if sort == "ok":
                    _SEEN["before"] = (row_mark(fields), _band(image))
                    break
                row = None
                gone = sort in ("gone", "lagged")
                break
            time.sleep(POLL_GAP)
        calibration._STEPS.append(
            (f"get_price: poll row 1 until it answers ({looks} look(s), "
             f"{reads} read(s))",
             (time.monotonic() - poll_started) * 1000 - sort_ms))
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
    return _priced(row, slot, text, verbose)


def _priced(row, slot, text, verbose):
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


def get_voucher_price(verbose=True):
    import recovery
    from open_inventory import press
    inv.focus_game()
    if not calibration._trade_window_open():
        if verbose:
            print("  the Trade window is shut; opening the Agent Shop.")
        shop.open_agent_shop(verbose=verbose)
        time.sleep(TAB_SETTLE)
    if not calibration.purchase_tab_showing():
        shop.click(*_need("purchase_tab"))
        time.sleep(TAB_SETTLE)

    bar = calibration._point(tuple(calibration._REG["purchase_search_bar"]))
    if verbose:
        print(f"  the text search at {bar}")
    calibration.click(*bar, settle=FIELD_SETTLE)
    press(_SHARED["input"]["VK_ESCAPE"])
    time.sleep(ACTION_GAP)
    recovery._type(VOUCHER_SEARCH)
    time.sleep(ACTION_GAP)

    band = calibration._box(tuple(calibration._REG["voucher_suggestions"]))
    seat = None
    for text, _conf, point in calibration.ocr(calibration.grab(), band):
        if VOUCHER_WORD.lower() in text.strip().lower():
            seat = point
            break
    if seat is None:
        seat = calibration._point(tuple(calibration._REG["voucher_gold"]))
        if verbose:
            print(f"  no {VOUCHER_WORD!r} read in the suggestions {band}; "
                  f"taking the calibrated seat {seat}")
    elif verbose:
        print(f"  {VOUCHER_WORD} sits at {seat} in the suggestions")
    calibration.click(*seat, settle=ACTION_GAP)

    button = calibration._point(
        tuple(calibration._REG["purchase_search_button"]))
    if verbose:
        print(f"  Search at {button}")
    calibration.click(*button, settle=ACTION_GAP)

    row, text = None, ""
    deadline = time.monotonic() + SEARCH_TIMEOUT
    while time.monotonic() < deadline:
        image = calibration.grab()
        text = read_row_one(image)
        if VOUCHER_WORD.lower() in text.lower():
            row = parse_fields(read_fields(image))
            if row is not None:
                break
        time.sleep(POLL_GAP)

    calibration.click(*bar, settle=FIELD_SETTLE)
    press(_SHARED["input"]["VK_ESCAPE"])
    calibration.park()

    if row is None:
        if verbose:
            print(f"  no {VOUCHER_WORD} row answered within "
                  f"{SEARCH_TIMEOUT:g}s; row 1 reads {text!r}")
        return None
    row["unit_price"] = row["price"]
    row["raw"] = text
    if verbose:
        print(f"  {row['name']}  qty {row['qty']}  "
              f"{row['unit_price']:,} a voucher")
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
