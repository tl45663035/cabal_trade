import re
import sys
import time

import calibration
import open_agent_shop_premium as shop
import open_inventory as inv

_SHARED = calibration.load_shared()
ACTION_GAP = _SHARED["timing"]["action_gap"]

_ROW = re.compile(r"^(?P<name>.*?)\s+(?P<qty>\d[\d,]*)\s+(?P<price>\d[\d,]*)\s*$")
_PACK = re.compile(r"\bX\s*(\d+)\s*$", re.IGNORECASE)
_LOW_TO_HIGH = re.compile(r"price\s*:?\s*low\s*to\s*high", re.IGNORECASE)


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
    return tuple(_need("sort_region"))


def purchase_row_one_box():
    return tuple(_need("purchase_row_one"))


def read_sort(image=None):
    image = image if image is not None else calibration.grab()
    tokens = calibration.ocr(image, sort_box())
    tokens.sort(key=lambda token: token[2][0])
    return " ".join(token[0] for token in tokens)


def sort_is_low_to_high(image=None):
    return _LOW_TO_HIGH.search(read_sort(image).replace(" ", " ")) is not None


def ensure_sort_low_to_high(verbose=True):
    if sort_is_low_to_high():
        if verbose:
            print("  sort confirmed Price: Low to High")
        return True
    raise NotReady(
        f"the sort does not read Price: Low to High -- it reads "
        f"{read_sort()!r}. Row 1 is only the cheapest under that sort, so "
        f"nothing here may be trusted until it is set.")


def parse_row(text):
    found = _ROW.match((text or "").strip())
    if found is None:
        return None
    name = found.group("name").strip(" |-")
    qty = int(found.group("qty").replace(",", ""))
    price = int(found.group("price").replace(",", ""))
    pack = _PACK.search(name)
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


def read_row_one(image=None):
    image = image if image is not None else calibration.grab()
    tokens = calibration.ocr(image, purchase_row_one_box())
    tokens.sort(key=lambda token: token[2][0])
    return " ".join(token[0] for token in tokens)


def get_price(slot, verbose=True):
    inv.focus_game()
    if not calibration._trade_window_open():
        raise NotReady("the Trade window is not open.")
    ensure_sort_low_to_high(verbose=verbose)

    x, y = favourite_point(slot)
    if verbose:
        print(f"  favourite slot {slot} at ({x}, {y})")
    shop.click(x, y)
    time.sleep(ACTION_GAP)

    text = read_row_one()
    row = parse_row(text)
    if row is None:
        if verbose:
            print(f"  row 1 did not parse: {text!r}")
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
