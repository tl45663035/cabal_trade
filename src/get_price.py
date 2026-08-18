import re
import sys
import time

import calibration
import open_agent_shop_premium as shop
import open_inventory as inv
import row_model

_SHARED = calibration.load_shared()
ACTION_GAP = _SHARED["timing"]["action_gap"]
TAB_SETTLE = _SHARED["timing"]["tab_settle"]
SEARCH_TIMEOUT = _SHARED["timing"]["search_timeout"]
RETRY_GAP = _SHARED["timing"]["retry_gap"]
RETRIES = _SHARED["timing"]["search_retries"]
EXPECTED = _SHARED["favourite_items"]

_NUMBER = re.compile(r"\d[\d,]*")
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
    found = _ROW.match(read_row_one(image).strip())
    return found.group("name").strip() if found else ""


def read_fields(image=None):
    image = image if image is not None else calibration.grab()
    whole = calibration.read_line(image, purchase_row_one_box()).strip()
    found = _ROW.match(whole)
    if found is None:
        return {"row": whole}
    return {
        "name": found.group("name"),
        "qty": found.group("qty"),
        "price": found.group("price"),
        "row": whole,
        "price_cell": calibration.read_line(
            image, column_box("price")).strip(),
    }


def read_sort(image=None):
    image = image if image is not None else calibration.grab()
    tokens = calibration.ocr(image, sort_box())
    tokens.sort(key=lambda token: token[2][0])
    return " ".join(token[0] for token in tokens)


def sort_is_low_to_high(image=None):
    found = _SORT_DIRECTION.search(read_sort(image))
    return found is not None and found.group(1).lower() == "low"


def ensure_sort_low_to_high(verbose=True):
    if sort_is_low_to_high():
        if verbose:
            print("  sort confirmed Price: Low to High")
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
    qty = _digits(fields.get("qty"))
    price = _digits(fields.get("price"))
    if not name or qty is None or price is None:
        return None
    anchor = _digits(fields.get("price_cell"))
    if anchor is not None and anchor != price:
        return None
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


def get_price(slot, verbose=True):
    inv.focus_game()
    if not calibration._trade_window_open():
        if verbose:
            print("  the Trade window is shut; opening the Agent Shop.")
        shop.open_agent_shop(verbose=verbose)
        time.sleep(TAB_SETTLE)
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
        before = row_name()
        shop.click(x, y)
        deadline = time.monotonic() + SEARCH_TIMEOUT
        while time.monotonic() < deadline:
            time.sleep(ACTION_GAP)
            image = calibration.grab()
            text = row_name(image)
            if text == before or not name_matches(slot, text):
                continue
            row = parse_fields(read_fields(image))
            if row is not None:
                break
        if row is not None:
            break
        if verbose:
            print(f"  attempt {attempt}/{RETRIES}: row 1 name reads {text!r}, "
                  f"expected {want!r} - retrying in {RETRY_GAP}s")
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
