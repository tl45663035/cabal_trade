
from __future__ import annotations

import argparse
import atexit
import csv
import ctypes
import ctypes.wintypes
import dataclasses as _dc
import io
import itertools
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
import datetime as _dt
from pathlib import Path


ITEM_PRICE_FLOORS: tuple[tuple[str, str, int], ...] = (
    ("vip", "Yekaterina VIP Membership", 104_000_000),
    ("siena", "Siena's Unbinding Stone", 71_000_000),
    ("gempack", "Force Gem Package (x400)", 175_000_000),
    ("epicboost", "Epic Booster (Highest)", 44_000_000),
)

FALLBACK_PRICE = 10_000_000_000

MIN_PLAUSIBLE_PRICE = 1_000

SUSPECT_PRICE_FRACTION = 0.5

RELATIVE_PRICE_FLOOR = 0.99


MAXIMISE_ALL_QUANTITIES = True

NO_MAX_QUANTITY_ITEMS: tuple[str, ...] = ()

MAX_QTY_ENTRY = 9999

QTY_CROSSCHECK_ABSOLUTE = 5

QTY_CROSSCHECK_FRACTION = 0.10


ACTION_COOLDOWN = 0.3

TYPE_COOLDOWN = 0.3

MIN_CYCLE_SECONDS = 1.0

RECEIVE_WAIT = 3.0

RELIST_ATTEMPTS = 3

SEND_ATTEMPTS = 4

CAPTURE_SUFFIX_LIMIT = 100

RELEASE_ATTEMPTS = 3

NPC_FIND_RETRIES = 4

CLOSE_DIALOG_TRIES = 4

INVENTORY_ORIGIN_RETRIES = 3

MAX_CONSECUTIVE_FAILURES = 3

TESSERACT_TIMEOUT = 30.0


GAME_TITLE_HINT = "PlayCabal"

SHOP_SESSION_SECONDS = 15 * 60
_shop_open_since: float | None = None

FAVOURITE_FIRST = (656, 1014)
FAVOURITE_PITCH = 57
FAVOURITE_COUNT = 10

FAVOURITE_SLOTS: dict[int, str] = {
    1:  "Force Core(Highest)",
    2:  "Force Core Set (Highest)",
    3:  "Chaos Core",
    4:  "Chaos Core Set",
    5:  "Force Core (Ultimate)",
    6:  "Force Core Set (Ultimate)",
    7:  "Force Core(High)",
    8:  "Force Core Set (High)",
    9:  "Upgrade Core (Ultimate)",
    10: "Upgrade Core Set (Ultimate)",
}


CHAOS_CORE_SLOT = 3
CHAOS_SET_SLOT = 4

CHAOS_SLOTS = frozenset({CHAOS_CORE_SLOT, CHAOS_SET_SLOT})

CHAOS_MARGIN_FLOOR = 10_000

CHAOS_HELD_OFF_ON_MARGIN = False
CHAOS_HELD_OFF_MARGIN: "int | None" = None

CHAOS_HELD_OFF_ON_LANDING = False

CHAOS_HELD_OFF = True

SHOP_MODEL_SHADOW = True


CHAOS_UNDERCUT = 1
CHAOS_RECIPE = 1

CHAOS_ENABLED = False

CHAOS_BUY_ORDERS = 1000
CHAOS_BUY_LOST_LIMIT = 3
CHAOS_ROWS = 2

SCREEN_ROWS = 10

CHAOS_RESTOCK_AT_OR_BELOW_ROWS = 2
CHAOS_MIDBATCH_TRIES = 3
CHAOS_BUY_QUANTITY = 100

PREMIUM_ENABLED = False
PREMIUM_SHOP_KEY_TAB = 8
PREMIUM_SHOP_KEY_SLOT = (1, 7)

CHAOS_CRAFT_TAB = 8
CHAOS_CRAFT_KEY_SLOT = (1, 8)
CHAOS_WORK_TAB = 4

CRAFT_RECIPES = {
    1: ((121, 236), (216, 318), "[1500] Chaos Core Set (x1)"),
    2: ((121, 276), (216, 359), "[2500] Chaos Core Set (x3)"),
}
CRAFT_REPEAT_POINT = (104, 981)
CRAFT_REQUEST_ALL = (355, 980)
CRAFT_COMPLETE_ALL = (1181, 980)
CRAFT_MATERIAL_REGION = (600, 640, 900, 690)
CRAFT_WINDOW_REGION = (10, 30, 1300, 1020)

CRAFT_SETTLE_PER_BLOCK_BY_RECIPE = {1: 15.0, 2: 5.0}
CRAFT_SETTLE_PER_BLOCK = 15.0
CRAFT_SETTLE_BLOCK = 50
CRAFT_SETTLE_MAX = 300.0


def craft_settle_rate() -> float:
    return CRAFT_SETTLE_PER_BLOCK_BY_RECIPE.get(
        int(CHAOS_RECIPE or 1), CRAFT_SETTLE_PER_BLOCK)


def craft_material_cost() -> int:
    return 3 if int(CHAOS_RECIPE or 1) == 2 else 1


def craft_settle_seconds(made: int) -> float:
    blocks = -(-max(0, int(made)) // CRAFT_SETTLE_BLOCK)
    return min(CRAFT_SETTLE_MAX, craft_settle_rate() * max(1, blocks))

_CRAFT_MATERIAL = re.compile(r"(\d+)\s*/\s*(\d+)")


def chaos_margin(core_price: "int | None",
                 set_price: "int | None",
                 set_pack: int = 1) -> "int | None":
    if not core_price or core_price <= 0:
        return None
    if not set_price or set_price <= 0:
        return None
    if set_pack < 1:
        return None
    return set_price // set_pack - core_price


def favourite_slot_point(slot: int) -> tuple[int, int]:
    if not 1 <= slot <= FAVOURITE_COUNT:
        raise ValueError(f"favourite slot {slot} is outside 1..{FAVOURITE_COUNT}")
    x, y = FAVOURITE_FIRST
    return (x + (slot - 1) * FAVOURITE_PITCH, y)


def favourite_for(item: str) -> int | None:
    want = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item)))
    if not want:
        return None
    for slot, name in FAVOURITE_SLOTS.items():
        if _floor_key(item_name(name)) == want:
            return slot
    return None


def favourite_set_slot(slot: int) -> int | None:
    partner = slot + 1
    name = FAVOURITE_SLOTS.get(slot, "")
    other = FAVOURITE_SLOTS.get(partner, "")
    if name and other and "set" in _floor_key(other) and \
            _floor_key(other).replace("set", "") == _floor_key(name):
        return partner
    return None


CONVERT_COLS = (252, 317, 381, 448, 512)
CONVERT_ROWS = (1066, 1133, 1197, 1258)
CONVERT_GRADES = ("Low", "Medium", "High", "Highest", "Ultimate")
CONVERT_QUANTITY = 250

CONVERT_TO_CORE = {
    (2, i + 1): (f"Force Core({g})" if g != "Ultimate" else "Force Core (Ultimate)",
                 f"Force Core Set ({g})")
    for i, g in enumerate(CONVERT_GRADES)
}
CONVERT_TO_CORE.update({
    (4, i + 1): (f"Upgrade Core({g})" if g != "Ultimate"
                 else "Upgrade Core (Ultimate)",
                 f"Upgrade Core Set ({g})")
    for i, g in enumerate(CONVERT_GRADES)
})
CONVERT_TO_SET = {(1, i + 1) for i in range(5)} | {(3, i + 1) for i in range(5)}


def convert_cell_point(row: int, col: int) -> tuple[int, int]:
    if not 1 <= row <= len(CONVERT_ROWS) or not 1 <= col <= len(CONVERT_COLS):
        raise ValueError(f"conversion cell ({row},{col}) is off the grid")
    return (CONVERT_COLS[col - 1], CONVERT_ROWS[row - 1])


SHOP_WINDOW_TITLE = (0, 150, 580, 240)
CONVERT_TIP_REGION = (0, 740, 900, 1400)


def vendor_shop_open(source: "Image.Image | None" = None) -> bool:
    shot = source if source is not None else grab()
    words = {w.text.casefold()
             for w in find_words(shot, SHOP_WINDOW_TITLE, 25) if w.conf >= 45}
    return {"shop", "normal", "repurchase"} <= words


CONVERT_TIP_SETTLES = (1.3, 1.8, 2.3, 3.0)


def _warm_text_image(image: "Image.Image",
                     region: tuple[int, int, int, int]) -> "Image.Image":
    r, g, b = image.crop(region).convert("RGB").split()
    return ImageChops.subtract(r, ImageChops.lighter(g, b))


def _price_from_lines(text: list[str]) -> tuple[str, int | None, int | None]:
    for i, line in enumerate(text):
        if line.casefold().startswith("price") and i + 1 < len(text):
            price_line = text[i + 1]
            found = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", price_line)
            if found:
                return (price_line,
                        int(found.group(1).replace(",", "")),
                        int(found.group(2).replace(",", "")))
            return price_line, None, None
    return "", None, None


def _tooltip_lines(shot: "Image.Image",
                   region: tuple[int, int, int, int]) -> list[str]:
    words = [w for w in find_words(shot, region, 25) if w.conf >= 40]
    lines: dict = {}
    for w in words:
        bucket = max(4, LAYOUT.length(16))
        lines.setdefault(round(w.centre[1] / bucket), []).append(w)
    out = []
    for key in sorted(lines):
        joined = " ".join(w.text for w in sorted(lines[key],
                                                 key=lambda w: w.centre[0]))
        if joined.strip():
            out.append(joined.strip())
    return out


def hover_tooltip(x: int, y: int, settle: float | None = None,
                  attempts: int | None = None,
                  need_price: bool = True,
                  region: tuple[int, int, int, int] | None = None) -> dict:
    focus_game()
    look = CONVERT_TIP_REGION if region is None else region

    waits = list(CONVERT_TIP_SETTLES) if settle is None else [settle]
    if attempts is not None:
        waits = (waits * attempts)[:max(1, attempts)]

    def attempt(wait: float) -> dict:
        move_mouse(x - LAYOUT.length(140), y - LAYOUT.length(140))
        time.sleep(0.18)
        move_mouse(x, y)
        time.sleep(wait)
        shot = grab()
        text = _tooltip_lines(shot, look)
        price_line, held, cost = _price_from_lines(text)

        if held is None or not need_price:
            warm = _warm_text_image(shot, look)
            warm_text = _tooltip_lines(warm, (0, 0, warm.width, warm.height))
            for line in warm_text:
                if line not in text:
                    text.append(line)
            if held is None:
                w_line, w_held, w_cost = _price_from_lines(warm_text)
                if w_held is not None:
                    price_line, held, cost = w_line, w_held, w_cost

        return {"lines": text, "price_line": price_line,
                "held": held, "cost": cost, "point": (x, y)}

    def good(tip: dict) -> bool:
        if need_price:
            return bool(tip["price_line"]) and tip["held"] is not None
        return bool(tip["lines"])

    best = attempt(waits[0])
    for wait in waits[1:]:
        if good(best):
            break
        move_mouse(x - LAYOUT.length(300), y - LAYOUT.length(300))
        time.sleep(0.25)
        best = attempt(wait)
    return best


def read_convert_tooltip(row: int, col: int, settle: float | None = None,
                         attempts: int | None = None) -> dict:
    return hover_tooltip(*convert_cell_point(row, col),
                         settle=settle, attempts=attempts, need_price=True)


def convert_cell_matches(row: int, col: int, tip: dict) -> bool:
    gives, costs = CONVERT_TO_CORE.get((row, col), ("", ""))
    if not gives:
        return False
    named = any(_names_agree(line, gives) for line in tip.get("lines", []))
    return named and _names_agree(tip.get("price_line", ""), costs)


def _convert_name_key(text: str) -> str:
    cleaned = re.sub(r"\d[\d,]*\s*/\s*\d[\d,]*\s*$", "", text.strip())
    cleaned = re.sub(r"^[^A-Za-z]+", "", cleaned)
    return _floor_key(item_name(cleaned))


def _names_agree(observed: str, expected: str) -> bool:
    return bool(observed) and _convert_name_key(observed) == _floor_key(
        item_name(expected))


def convert_cell_for(core_name: str) -> "tuple[int, int] | None":
    want = _floor_key(item_name(core_name))
    for (row, col), (gives, _costs) in CONVERT_TO_CORE.items():
        if _floor_key(item_name(gives)) == want:
            return (row, col)
    return None


CONVERT_INVENTORY_TAB = 4
CONVERT_SET_SLOT = (1, 1)
CONVERT_CORE_SLOT = (1, 2)


def slot_tip_region(x: int, y: int) -> tuple[int, int, int, int]:
    return (max(0, x - LAYOUT.length(560)), max(0, y - LAYOUT.length(70)),
            max(1, x - LAYOUT.length(20)), y + LAYOUT.length(430))


def read_slot_tooltip(row: int, col: int, settle: float | None = None) -> dict:
    x, y = slot_centre(row, col)
    return hover_tooltip(x, y, settle=settle, need_price=False,
                         region=slot_tip_region(x, y))


CONVERT_DIALOG_REGION = (975, 470, 1570, 945)
CONVERT_DLG_ITEM = (1000, 538, 1420, 576)
CONVERT_DLG_PRICE = (1160, 650, 1520, 692)
CONVERT_DLG_QTY_VALUE = (1163, 592, 1252, 630)
CONVERT_DLG_QTY_MAX = (1268, 594, 1338, 628)
CONVERT_DIALOG_BUTTONS = (1150, 880, 1560, 940)
CONVERT_CONFIRM_WORD = "OK"
CONVERT_CANCEL_WORD = "Cancel"
CONVERT_DIALOG_TIMEOUT = 8.0


def mass_purchase_open(
    source: "Image.Image | None" = None,
) -> "tuple[Word, Word] | None":
    shot = source if source is not None else grab()
    ok = find_text(shot, CONVERT_CONFIRM_WORD, CONVERT_DIALOG_BUTTONS, 40.0)
    cancel = find_text(shot, CONVERT_CANCEL_WORD, CONVERT_DIALOG_BUTTONS, 40.0)
    qty = find_text(shot, "QTY", CONVERT_DIALOG_REGION, 40.0)
    if not ok or not cancel or not qty:
        return None
    return ok[-1], cancel[-1]


def mass_purchase_details(source: "Image.Image | None" = None) -> dict:
    shot = source if source is not None else grab()

    def line(region: tuple[int, int, int, int]) -> str:
        words = [w for w in find_words(shot, region, 25) if w.conf >= 30]
        return " ".join(w.text for w in sorted(words, key=lambda w: w.centre[0]))

    price_line = line(CONVERT_DLG_PRICE)
    held = cost = None
    match = re.search(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)", price_line)
    if match:
        held = int(match.group(1).replace(",", ""))
        cost = int(match.group(2).replace(",", ""))
    return {
        "item": line(CONVERT_DLG_ITEM),
        "price_line": price_line,
        "held": held,
        "cost": cost,
        "qty": read_number(shot, CONVERT_DLG_QTY_VALUE),
        "qty_max": read_number(shot, CONVERT_DLG_QTY_MAX),
    }


def mass_purchase_matches(row: int, col: int, detail: dict) -> bool:
    gives, costs = CONVERT_TO_CORE.get((row, col), ("", ""))
    if not gives:
        return False
    return (_names_agree(detail.get("item", ""), gives)
            and _names_agree(detail.get("price_line", ""), costs))


def core_slot_candidates(before: set, after: set) -> list:
    fresh = sorted(after - before)
    persisted = sorted(after & before)
    return fresh + persisted


def convert_cores(core_name: str, quantity: int = CONVERT_QUANTITY,
                  verbose: bool = True, execute: bool = True,
                  require_layout: bool = True) -> dict:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    cell = convert_cell_for(core_name)
    require(cell is not None,
            f"{core_name!r} is not something the vendor converts Sets into. "
            "Set names resolve to nothing on purpose: converting Cores into "
            "Sets is the losing side of this trade.")
    row, col = cell
    require((row, col) not in CONVERT_TO_SET,
            f"cell r{row}c{col} is a CORE -> SET cell and must never be clicked")
    gives, costs = CONVERT_TO_CORE[(row, col)]

    require(focus_game(), "could not bring Cabal to the foreground")
    require(vendor_shop_open(),
            "the vendor's Shop window is not open on the Normal tab. This "
            "function clicks inside a shop, where a click spends something "
            "with no confirmation, so it will not act on an unrecognised "
            "screen.")

    say(f"Converting Sets -> {gives} (r{row}c{col}, paying with {costs})")

    stale = mass_purchase_open()
    if stale is not None:
        say("  a Purchase Item dialog was already open; cancelling it first")
        click(*stale[1].centre)
        time.sleep(0.5)
        if mass_purchase_open() is not None:
            press_escape()
            time.sleep(0.3)
        require(mass_purchase_open() is None,
                "a Purchase Item dialog is open and will not close. Refusing "
                "to click the grid underneath an unrecognised modal.")

    origin = inventory_origin()
    require(origin is not None,
            "the Inventory panel is not open, so there is no way to see where "
            "the Cores end up. Open it before converting.")
    require(select_inventory_tab(CONVERT_INVENTORY_TAB, origin),
            f"could not put the Inventory on tab {CONVERT_INVENTORY_TAB}. The "
            "slot checks below only mean anything on the tab that holds the "
            "Sets, so there is nothing safe to do without it.")

    filled = set(occupied_slots(grab(), origin))
    if require_layout:
        require(CONVERT_SET_SLOT in filled,
                f"inventory slot {CONVERT_SET_SLOT} looks empty")
        require(CONVERT_CORE_SLOT not in filled,
                f"inventory slot {CONVERT_CORE_SLOT} is already occupied. It "
                "is the landing slot for the converted Cores, and an occupied "
                "one makes the after-check meaningless -- it would read as "
                "success whatever happened. Clear it first.")
        say(f"  inventory tab {CONVERT_INVENTORY_TAB}: {CONVERT_SET_SLOT} "
            f"holds {costs}, {CONVERT_CORE_SLOT} is clear")
    else:
        free = GRID_SIZE * GRID_SIZE - len(filled)
        if free <= 0:
            say(f"  inventory tab {CONVERT_INVENTORY_TAB} is full; the Cores "
                "will land on a later tab and this tab's count will read 0. "
                "The listing quantity is what counts them.")
        else:
            say(f"  inventory tab {CONVERT_INVENTORY_TAB}: {len(filled)} "
                f"slot(s) used, {free} free")

    if not execute:
        return {"cell": (row, col), "gives": gives, "costs": costs,
                "would_convert": quantity, "converted": 0}

    require(vendor_shop_open(),
            "the vendor's Shop window closed before the click. Nothing was "
            "clicked.")
    showing = active_vendor_tab()
    if showing != CONVERT_VENDOR_TAB:
        time.sleep(0.5)
        again = active_vendor_tab()
        if again == CONVERT_VENDOR_TAB:
            say(f"  the vendor tab read as {showing or 'unidentifiable'} and "
                f"then as {again!r} - taking the second reading.")
            showing = again
    require(showing == CONVERT_VENDOR_TAB,
            f"the vendor Shop is showing {showing or 'a tab I cannot identify'}"
            f", not {CONVERT_VENDOR_TAB}. The conversion grid is only on that "
            "tab; these coordinates mean something else here.")

    x, y = convert_cell_point(row, col)
    alt_click(x, y)

    deadline = time.monotonic() + CONVERT_DIALOG_TIMEOUT
    buttons = None
    while buttons is None and time.monotonic() < deadline:
        buttons = mass_purchase_open()
        if buttons is None:
            time.sleep(0.4)
    if buttons is None:
        press_escape()
        raise Aborted(
            "the Purchase Item dialog did not appear after Alt+click. Nothing "
            "was confirmed. If Alt+click is not the gesture on this client, "
            "the plain click is an IMMEDIATE purchase and must NOT be "
            "substituted for it.")
    confirm, cancel = buttons

    def bail(reason: str) -> None:
        try:
            click(*cancel.centre)
            time.sleep(0.4)
            if mass_purchase_open() is not None:
                press_escape()
        except Exception:
            press_escape()
        park_cursor()
        raise Aborted(reason)

    detail = mass_purchase_details()
    if not mass_purchase_matches(row, col, detail):
        bail(f"the Purchase Item dialog is not the trade that was intended. "
             f"Expected {gives} paid for with {costs}; the dialog says "
             f"{detail.get('item')!r} paid for with "
             f"{detail.get('price_line')!r}. Cancelled without buying.")
    if detail.get("cost") not in (None, 1):
        bail(f"the dialog prices this at {detail['cost']} per conversion, not "
             "1. Every cell in this grid is a one-for-one exchange, so a "
             "different cost means it is not the trade it was mapped as.")

    limit = detail.get("qty_max")
    if limit is None:
        limit = detail.get("held")
    if limit is not None and limit <= 0:
        bail(f"the dialog offers a maximum of {limit} - no {costs} to convert")

    free = GRID_SIZE * GRID_SIZE - len(filled)
    record("convert.dialog", item=core_name, cell=f"r{row}c{col}",
           limit=limit, asked=quantity, free=free)

    say(f"  typing {quantity}; the dialog clamps it to what is held"
        + (f" (it reads a maximum of {limit})" if limit is not None else ""))

    click((CONVERT_DLG_QTY_VALUE[0] + CONVERT_DLG_QTY_VALUE[2]) // 2,
          (CONVERT_DLG_QTY_VALUE[1] + CONVERT_DLG_QTY_VALUE[3]) // 2)
    time.sleep(0.25)
    if not mass_purchase_open():
        bail("the Purchase Item dialog closed when the quantity field was "
             "clicked, so the keystrokes would have gone to the game world. "
             "Cancelled without typing anything.")

    type_number(quantity, clear_first=True, clear=6)

    if not mass_purchase_open():
        bail("the Purchase Item dialog vanished while the quantity was being "
             "typed, so some of those keystrokes reached the game. Cancelled "
             "without buying.")

    park_cursor()
    time.sleep(0.35)

    settled = mass_purchase_details().get("qty")
    for _ in range(QTY_READBACK_TRIES):
        if settled is not None and 1 <= settled <= quantity:
            break
        time.sleep(QTY_READBACK_PAUSE)
        settled = mass_purchase_details().get("qty")
    record("convert.typed", item=core_name, typed=quantity,
           limit=limit, landed=settled)
    if settled is None:
        say("  the quantity field could not be read after typing; proceeding "
            "anyway -- the dialog clamps to what is held, and the conversion "
            "is counted from the slots that fill")
        expected = min(quantity, limit) if limit else quantity
    elif settled > quantity:
        bail(f"typed {quantity} but the field reads {settled}, which is MORE "
             "than was asked for. Cancelled without buying.")
    elif settled < 1:
        bail(f"typed {quantity} but the field reads {settled}. Cancelled "
             "without buying.")
    else:
        expected = settled
        if expected < quantity:
            say(f"  the dialog clamped {quantity} to {expected} -- that is "
                "what is held; the remainder comes in a later round")

    countable = min(expected, free)
    if free <= 0:
        countable = 0
    if expected > free:
        say(f"  note: only {free} free slot(s) on tab {CONVERT_INVENTORY_TAB}; "
            f"Cores beyond that land on a later tab and are not counted here")

    if not vendor_shop_open():
        bail("the vendor's Shop window is no longer open behind the dialog. "
             "Cancelled without buying.")

    say(f"  quantity {expected} confirmed in the field; purchasing")
    record("convert.confirming", item=core_name, quantity=expected)
    click(*confirm.centre)
    time.sleep(0.8)

    if mass_purchase_open() is not None:
        press_escape()
        time.sleep(0.3)

    select_inventory_tab(CONVERT_INVENTORY_TAB, origin)
    filled_after = set(occupied_slots(grab(), origin))
    park_cursor()
    arrived = filled_after - filled
    converted = len(arrived)
    landed = bool(filled_after - filled)
    candidates = core_slot_candidates(filled, filled_after)

    if arrived:
        where = sorted(arrived)
        say(f"  new Cores landed in {len(where)} slot(s) on tab "
            f"{CONVERT_INVENTORY_TAB}: {where[0]} to {where[-1]}"
            + (f" -- e.g. {where[:6]}" if len(where) > 1 else ""))
    if converted == countable and landed:
        extra = "" if countable == expected else f" (+{expected - countable} on later tabs)"
        say(f"  converted {converted} x {costs} -> {gives}{extra}")
    elif converted:
        say(f"  WARNING: expected {countable} on this tab but {converted} "
            "slot(s) filled. Nothing further was clicked.")
    else:
        say("  WARNING: no new inventory slots were filled. The purchase may "
            "not have gone through.")

    return {"cell": (row, col), "gives": gives, "costs": costs,
            "expected": expected, "countable": countable,
            "converted": converted, "arrived": sorted(arrived),
            "candidates": candidates,
            "occupied_before": sorted(filled),
            "occupied_after": sorted(filled_after),
            "landed": landed, "verified": converted == countable and landed}


PURCHASE_ROW_TOP = 340
PURCHASE_ROW_PITCH = 76
PURCHASE_ROWS = 8

PURCHASE_ROWS_DEFAULT = 1
PURCHASE_DIALOG_REGION = (700, 400, 1700, 950)
PURCHASE_DLG_ITEM = (1030, 600, 1330, 645)
CRAFT_MATERIAL_SETTLE = 0.8

QTY_READBACK_TRIES = 3
QTY_READBACK_PAUSE = 0.2

ALZ_SETTLE_BUDGET = 6.0
ALZ_SETTLE_POLL = 0.6

PURCHASE_DLG_QTY_VALUE = (1152, 668, 1218, 702)
PURCHASE_DLG_QTY_MAX = (1215, 665, 1296, 705)
PURCHASE_DLG_PRICE = (1150, 712, 1380, 758)
PURCHASE_DIALOG_BUTTONS_Y = 800
PURCHASE_DIALOG_BUTTONS = (1190, 830, 1570, 880)
PURCHASE_CANCEL_DX = 180
CONFIRM_RECLICKS = 3
CHAOS_TOPUP_ORDERS = 4

PURCHASE_NAME_MAX_X = 700
PURCHASE_PRICE_X = (900, 1080)
PURCHASE_BUY_X = 1124

PURCHASE_ROW_BAND_X = (250, 1235)
PURCHASE_ROW_HALF = 24
PURCHASE_ROW_SELECT_X = 500
PRICE_DIFF_FLOOR = 10_000
SET_SAVING_THRESHOLD = PRICE_DIFF_FLOOR

PRICE_DIFF_FLOOR_BY_ITEM: dict[str, int] = {
    "Force Core(High)":      5_000,
    "Force Core(Highest)":   5_000,
}


def price_diff_floor_for(item: str) -> int:
    want = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item or "")))
    if not want:
        return PRICE_DIFF_FLOOR
    for name, floor in PRICE_DIFF_FLOOR_BY_ITEM.items():
        if _floor_key(item_name(name)) == want:
            return int(floor)
    return PRICE_DIFF_FLOOR


def validate_price_diff_floors() -> None:
    known = {_floor_key(item_name(n)) for n in FAVOURITE_SLOTS.values()}
    for name in PRICE_DIFF_FLOOR_BY_ITEM:
        if _floor_key(item_name(name)) not in known:
            raise ValueError(
                f"PRICE_DIFF_FLOOR_BY_ITEM names {name!r}, which is not a "
                "favourite. Known: "
                + ", ".join(sorted(FAVOURITE_SLOTS.values())))
BUY_RETRY_ATTEMPTS = 3

FAVOURITE_SEARCH_TRIES = 5
PRICE_OUTLIER_FLOOR = 0.5

_PACK_SIZE = re.compile(r"(?:\bX\s*|(?<=[A-Za-z])X)([\d,]+)\s*$", re.IGNORECASE)
_PACK_ANYWHERE = re.compile(r"(?:\bX\s*|(?<=[A-Za-z])X)[\d,]+", re.IGNORECASE)


def pack_size(name: str) -> int:
    m = _PACK_SIZE.search(name.strip())
    if not m:
        return 1
    try:
        return max(1, int(m.group(1).replace(",", "")))
    except ValueError:
        return 1


@dataclass(frozen=True)
class Offer:
    row: int
    name: str
    price: int
    pack: int
    y: int
    available: int = 1

    @property
    def unit(self) -> float:
        return self.price / self.pack

    @property
    def stock(self) -> int:
        return self.pack * max(1, self.available)


def read_purchase_rows(source: "Image.Image | None" = None,
                       rows: "int | None" = None) -> list[Offer]:
    shot = source if source is not None else grab()
    offers: list[Offer] = []

    wanted = PURCHASE_ROWS_DEFAULT if rows is None else max(1, int(rows))
    for i in range(min(wanted, PURCHASE_ROWS)):
        y = PURCHASE_ROW_TOP + i * PURCHASE_ROW_PITCH
        band = (PURCHASE_ROW_BAND_X[0], y - PURCHASE_ROW_HALF,
                PURCHASE_ROW_BAND_X[1], y + PURCHASE_ROW_HALF)
        words = [w for w in find_words(shot, band, 20) if w.conf >= 55]
        if not words:
            continue
        name = " ".join(w.text for w in sorted(
            (w for w in words if w.centre[0] < PURCHASE_NAME_MAX_X),
            key=lambda w: w.centre[0])).strip()
        price = None
        for w in words:
            if PURCHASE_PRICE_X[0] < w.centre[0] < PURCHASE_PRICE_X[1]:
                digits = re.sub(r"[^\d]", "", w.text)
                if digits.isdigit() and len(digits) >= 4:
                    price = int(digits)
        if not name or price is None or price < MIN_PLAUSIBLE_PRICE:
            continue

        cell = (PURCHASE_NAME_MAX_X, y - PURCHASE_ROW_HALF,
                PURCHASE_PRICE_X[0], y + PURCHASE_ROW_HALF)
        available = None
        digits = sorted((w for w in words
                         if PURCHASE_NAME_MAX_X < w.centre[0]
                         < PURCHASE_PRICE_X[0]),
                        key=lambda w: w.centre[0])
        if digits:
            joined = re.sub(r"[^\d]", "", "".join(w.text for w in digits))
            if joined.isdigit():
                available = int(joined)
        if available is None:
            available = read_number(shot, cell, 30.0)
        if not available or available < 1:
            available = 1

        offers.append(Offer(row=i + 1, name=name, price=price,
                            pack=pack_size(name), y=y, available=available))
    return offers


def _row_one(offers: "list[Offer]") -> "Offer | None":
    for offer in offers or []:
        if getattr(offer, "row", None) == 1:
            return offer
    return None


def cheapest_per_unit(offers: list[Offer]) -> "Offer | None":
    if not offers:
        return None
    if len(offers) >= 3:
        units = sorted(o.unit for o in offers)
        median = units[len(units) // 2]
        credible = [o for o in offers if o.unit >= median * PRICE_OUTLIER_FLOOR]
        if credible:
            offers = credible
    return min(offers, key=lambda o: o.unit)


def credible_offers(offers: list[Offer]) -> list[Offer]:
    if len(offers) < 3:
        return list(offers)
    units = sorted(o.unit for o in offers)
    median = units[len(units) // 2]
    kept = [o for o in offers if o.unit >= median * PRICE_OUTLIER_FLOOR]
    return kept or list(offers)


def cheapest_listing(offers: list[Offer]) -> "Offer | None":
    return _row_one(offers)


def purchase_confirm(source: "Image.Image | None" = None) -> dict | None:
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, PURCHASE_DIALOG_REGION, 20)
             if w.conf >= 45]
    text = " ".join(w.text for w in words)
    if "Purchase" not in text:
        return None
    buttons = {}
    for w in words:
        label = w.text.strip().lower()
        if label in ("buy", "cancel") and w.centre[1] > PURCHASE_DIALOG_BUTTONS_Y:
            buttons[label] = w.centre
    if "buy" not in buttons:
        band = dialog_button_band("Buy", source=shot)
        if band is not None:
            buttons["buy"] = band.centre
    if "cancel" not in buttons:
        band = dialog_button_band(DISMISS_WORD, source=shot)
        if band is not None:
            buttons["cancel"] = band.centre
    if "buy" not in buttons:
        return None
    price = read_number(shot, PURCHASE_DLG_PRICE, 40.0)
    if price is None:
        for w in words:
            digits = re.sub(r"[^\d]", "", w.text)
            if digits.isdigit() and len(digits) >= 6:
                price = int(digits)

    qty = read_number(shot, PURCHASE_DLG_QTY_VALUE, 30.0)

    qty_max = read_number(shot, PURCHASE_DLG_QTY_MAX, 20.0)

    cancel = buttons.get("cancel")
    if cancel is None and "buy" in buttons:
        cancel = (buttons["buy"][0] + PURCHASE_CANCEL_DX, buttons["buy"][1])
    return {"buy": buttons["buy"], "cancel": cancel,
            "price": price, "text": text, "qty": qty, "qty_max": qty_max}


def dismiss_purchase_dialog(verbose: bool = False) -> bool:
    dialog = purchase_confirm()
    if dialog is None:
        return True
    target = dialog.get("cancel")
    if target is None:
        return False
    if verbose:
        print(f"  dismissing the Confirm Purchase dialog at {target}")
    click(*target)
    time.sleep(1.0)
    return purchase_confirm() is None


def offers_match_slot(slot: int, offers: list[Offer]) -> bool:
    want = FAVOURITE_SLOTS.get(slot)
    if not want:
        return False
    key = _floor_key(item_name(want))
    hits = 0
    for offer in offers:
        bare = _PACK_SIZE.sub("", offer.name).strip()
        if _floor_key(item_name(bare)) == key:
            hits += 1
    return hits >= max(1, len(offers) // 2)


PURCHASE_TAB_MARKERS = ("Category", "Function")
PURCHASE_SORT_REGION = (820, 178, 1080, 212)

PURCHASE_SORT_BUTTON = (930, 195)
PURCHASE_SORT_OPTIONS = (790, 212, 1120, 285)

_SORT_DIRECTION = re.compile(r"price\s*:?\s*(low|high)", re.IGNORECASE)

PURCHASE_SORT_TRIES = 3


def purchase_tab_open(source: "Image.Image | None" = None) -> bool:
    shot = source if source is not None else grab()
    hits = sum(1 for marker in PURCHASE_TAB_MARKERS
               if find_text(shot, marker, TRADE_TOP_BAND))
    return hits >= len(PURCHASE_TAB_MARKERS)


def purchase_sorted_low_to_high(source: "Image.Image | None" = None) -> bool:
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, PURCHASE_SORT_REGION, 20)
             if w.conf >= 45]
    text = " ".join(w.text for w in words)
    match = _SORT_DIRECTION.search(text)
    if match is None:
        return False
    return match.group(1).casefold() == "low"


def _sort_option_rows(source: "Image.Image | None" = None) -> dict:
    shot = source if source is not None else grab()
    words = [w for w in find_words(shot, PURCHASE_SORT_OPTIONS, 20)
             if w.conf >= 45]
    rows: dict = {}
    for line in _text_lines(words):
        text = " ".join(w.text for w in line)
        match = _SORT_DIRECTION.search(text)
        if match is None:
            continue
        lowered = text.casefold()
        if "low" not in lowered or "high" not in lowered:
            continue
        left = min(w.left for w in line)
        right = max(w.right for w in line)
        top = min(w.top for w in line)
        bottom = max(w.bottom for w in line)
        rows[match.group(1).casefold()] = ((left + right) // 2,
                                           (top + bottom) // 2)
    return rows


SORT_OPTIONS_TIMEOUT = 2.0


def _wait_for_sort_options(timeout: float = SORT_OPTIONS_TIMEOUT) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        rows = _sort_option_rows()
        if "low" in rows:
            return rows
        if time.monotonic() >= deadline:
            return rows
        time.sleep(0.2)


def set_purchase_sort_low_to_high(verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    for attempt in range(1, PURCHASE_SORT_TRIES + 1):
        shot = grab()
        if not trade_window_open(shot):
            say("  the Trade window is not open - refusing to click the sort "
                "control, the game world is underneath.")
            record("sort.not_ready", reason="window_shut")
            return False
        if not purchase_tab_open(shot):
            say("  not on the Purchase tab - the sort control does not exist "
                "on the other tab.")
            record("sort.not_ready", reason="wrong_tab")
            return False

        rows = _sort_option_rows(shot)
        if not rows:
            if purchase_sorted_low_to_high(shot):
                say("  the sort is already Price: Low to High.")
                return True
            say(f"  the sort is not Price: Low to High; opening the dropdown "
                f"(try {attempt} of {PURCHASE_SORT_TRIES}).")
            if not panel_covers_trade_area():
                say("  the Trade area is still moving - that is the world, "
                    "not a panel. Refusing to click.")
                record("sort.not_ready", reason="area_animating")
                return False
            click(*PURCHASE_SORT_BUTTON)
            rows = _wait_for_sort_options()

        target = rows.get("low")
        if target is None:
            say("  the dropdown did not open, or 'Price: Low to High' was not "
                "legible in it - not clicking a row I cannot read.")
            record("sort.option_unread", seen=sorted(rows))
            continue

        say(f"  selecting 'Price: Low to High' at {target}.")
        click(*target)
        time.sleep(0.4)
        if purchase_sorted_low_to_high():
            say("  the sort now reads Price: Low to High.")
            record("sort.set", attempt=attempt)
            return True
        say("  the sort still does not read Price: Low to High.")

    say(f"  could not set the sort to Price: Low to High in "
        f"{PURCHASE_SORT_TRIES} tries.")
    record("sort.failed", tries=PURCHASE_SORT_TRIES)
    return False


def purchase_ready(verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = grab()
    if not trade_window_open(shot):
        say("  the Trade window is not open - refusing to click, the game "
            "world is underneath.")
        record("purchase.not_ready", reason="window_shut")
        return False
    if not purchase_tab_open(shot):
        say("  the Trade window is not on the Purchase tab - refusing to "
            "click Purchase-tab coordinates on another tab.")
        record("purchase.not_ready", reason="wrong_tab")
        return False
    if not purchase_sorted_low_to_high(shot):
        say("  the results are not sorted Price: Low to High, so 'row 1 is "
            "the cheapest' does not hold. Refusing.")
        record("purchase.not_ready", reason="wrong_sort")
        return False
    if not panel_covers_trade_area():
        say("  the Trade window reads as open but the area is still moving - "
            "that is the world, not a panel. Refusing to click.")
        record("purchase.not_ready", reason="area_animating")
        return False
    return True


BUY_ROW = 1
SEARCH_RECEIPT_SECONDS = 90.0
_LAST_SEARCH: dict | None = None


def note_favourite_search(slot: int, offers: list) -> None:
    global _LAST_SEARCH
    _LAST_SEARCH = {
        "slot": slot,
        "at": time.monotonic(),
        "first": offers[0].name if offers else "",
    }


def search_receipt_for(offer) -> str:
    if offer.row != BUY_ROW:
        return (f"row {offer.row} is not row {BUY_ROW}; only the first row of "
                "a favourite search may be bought")
    if _LAST_SEARCH is None:
        return "no favourite search has run, so there is no row 1 to speak of"
    age = time.monotonic() - _LAST_SEARCH["at"]
    if age > SEARCH_RECEIPT_SECONDS:
        return (f"the last favourite search was {age:.0f}s ago; too old to "
                "trust that these rows are still the ones it found")
    if not _LAST_SEARCH["first"]:
        return "the last favourite search returned no rows at all"
    shown = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", _LAST_SEARCH["first"])))
    wanted = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", offer.name)))
    if not shown or not wanted:
        return "either the search or the offer has no readable name"
    if shown != wanted:
        return (f"row 1 of the last search was {_LAST_SEARCH['first']!r}, not "
                f"{offer.name!r}")
    return ""


SEARCH_POLL_SECONDS = 0.2
SEARCH_FLOOR_SECONDS = 0.15


def run_favourite_search(slot: int, settle: float = 3.0,
                         tries: int = FAVOURITE_SEARCH_TRIES,
                         verbose: bool = True) -> list[Offer]:
    for attempt in range(1, tries + 1):
        if not purchase_ready(verbose=verbose):
            if verbose:
                print(f"  slot {slot}: the Purchase tab is not ready "
                      f"(tab, sort, or the window itself) - the search was "
                      f"never run.")
            record("search.not_ready", slot=slot, attempt=attempt)
            return []
        prior = read_purchase_rows(rows=1)
        ambiguous = bool(prior) and offers_match_slot(slot, prior)

        x, y = favourite_slot_point(slot)
        focus_game()
        move_mouse(x, y - LAYOUT.length(45))
        click(x, y)
        park_cursor()
        time.sleep(SEARCH_FLOOR_SECONDS)
        offers = read_purchase_rows()
        if ambiguous:
            time.sleep(max(0.0, settle - SEARCH_FLOOR_SECONDS))
            offers = read_purchase_rows()
        else:
            deadline = time.monotonic() + settle
            while not (offers and offers_match_slot(slot, offers)):
                if time.monotonic() >= deadline:
                    break
                time.sleep(SEARCH_POLL_SECONDS)
                offers = read_purchase_rows()

        if offers and offers_match_slot(slot, offers):
            if verbose:
                print(f"  slot {slot} ({FAVOURITE_SLOTS.get(slot, '?')}): "
                      f"{len(offers)} offer(s)")
            note_favourite_search(slot, offers)
            return offers
        if verbose:
            sample = offers[0].name if offers else "(nothing)"
            print(f"  slot {slot}: the results still show {sample!r} - the "
                  f"search did not run (attempt {attempt}/{tries})")
        if table_loading(grab()):
            wait_for_table(timeout=max(6.0, settle * 4), verbose=verbose)
    return []


RACE_REFUSALS = ("sold out", "sold to another")

TRANSIENT_REFUSALS = (
    "the dialog says",
    "did not appear",
    "vanished",
    "did not read",
)


def is_retryable_refusal(why: str) -> bool:
    text = (why or "").lower()
    return (is_race_refusal(text)
            or any(marker in text for marker in TRANSIENT_REFUSALS))


def is_race_refusal(why: str) -> bool:
    text = (why or "").lower()
    return any(marker in text for marker in RACE_REFUSALS)


def buy_offer(offer: Offer, want: int = 1, timeout: float = 8.0,
              report: "dict | None" = None,
              verbose: bool = True) -> tuple[bool, str]:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not purchase_ready(verbose=verbose):
        return False, "the Purchase tab was not ready to be clicked"

    wrong = search_receipt_for(offer)
    if wrong:
        halt_buying(f"a Buy was attempted outside the sanctioned sequence: "
                    f"{wrong}")
        return False, wrong
    before = get_alz(grab()) or None
    focus_game()
    move_mouse(PURCHASE_ROW_SELECT_X, offer.y - LAYOUT.length(40))
    time.sleep(0.2)
    click(PURCHASE_ROW_SELECT_X, offer.y)
    time.sleep(0.8)
    move_mouse(PURCHASE_BUY_X, offer.y - LAYOUT.length(40))
    time.sleep(0.2)
    click(PURCHASE_BUY_X, offer.y)
    time.sleep(1.5)

    park_cursor()
    time.sleep(0.3)

    dialog = purchase_confirm()
    if dialog is None:
        moved = None
        try:
            after_blind = get_alz(grab())
            if before and after_blind and after_blind < before:
                moved = before - after_blind
        except Exception:
            moved = None
        record("buy.no_dialog", item=offer.name, price=offer.price,
               spent=moved or 0)
        if moved:
            halt_buying(f"the Confirm Purchase dialog never appeared, but "
                        f"{moved:,} Alz left the account - something was "
                        f"bought that this run cannot account for")
            return False, (f"the dialog did not appear AND {moved:,} Alz was "
                           f"spent - buying halted for a human to look")
        dismiss_purchase_dialog()
        return False, "the Confirm Purchase dialog did not appear"

    record("buy.dialog", item=offer.name, price=offer.price,
           pack=offer.pack, available=offer.available)

    def refuse(why: str) -> tuple[bool, str]:
        say(f"  {why} - cancelling rather than buying it.")
        record("buy.refused", item=offer.name, price=offer.price, why=why)
        if dialog and dialog.get("cancel"):
            cx, cy = dialog["cancel"]
            move_mouse(cx, cy + LAYOUT.length(60))
            time.sleep(0.2)
            click(cx, cy)
            time.sleep(1.0)
        return False, why

    limit = dialog.get("qty_max")

    for _ in range(QTY_READBACK_TRIES):
        if limit and int(limit) >= 1:
            break
        time.sleep(QTY_READBACK_PAUSE)
        again = purchase_confirm()
        if again is None:
            break
        dialog = again
        limit = dialog.get("qty_max")

    if not limit or limit < 1:
        say(f"  the dialog's quantity limit did not read in "
            f"{QTY_READBACK_TRIES} attempts - taking one listing.")
        limit = 1
    take = max(1, min(int(want), int(limit), max(1, offer.available)))
    if report is not None:
        report["take"] = take
        report["items"] = take * max(1, offer.pack)

    if take > 1:
        pack = max(1, offer.pack)
        available = max(1, offer.available)
        if pack > 1:
            asked = take
        elif take >= available:
            asked = -(-take // BUY_QTY_GRANULARITY) * BUY_QTY_GRANULARITY
        else:
            asked = take
        take = min(asked, available)
        if report is not None:
            report["take"] = take
            report["items"] = take * max(1, offer.pack)
        if asked != take:
            say(f"  taking {take} of the {limit} listing(s) on offer "
                f"(typing {asked}; the dialog clamps to the row)")
        else:
            say(f"  taking {take} of the {limit} listing(s) on offer")
        click((PURCHASE_DLG_QTY_VALUE[0] + PURCHASE_DLG_QTY_VALUE[2]) // 2,
              (PURCHASE_DLG_QTY_VALUE[1] + PURCHASE_DLG_QTY_VALUE[3]) // 2)
        type_number(asked, clear_first=True, clear=6)
        park_cursor()
        time.sleep(0.35)

        dialog = purchase_confirm()
        if dialog is None:
            dismiss_purchase_dialog()
            return False, ("the Confirm Purchase dialog vanished while the "
                           "quantity was being typed")

    expected = offer.price * take

    for _ in range(QTY_READBACK_TRIES):
        if dialog.get("price") == expected:
            break
        time.sleep(QTY_READBACK_PAUSE)
        again = purchase_confirm()
        if again is None:
            dismiss_purchase_dialog()
            return False, ("the Confirm Purchase dialog vanished while the "
                           "price was being read")
        dialog = again
    if not dialog.get("price"):
        return refuse("the dialog's Purchase Price did not read, and it is "
                      "the only confirmation that the quantity landed")
    if dialog["price"] != expected:
        return refuse(f"the dialog says {dialog['price']:,} but {take} x "
                      f"{offer.price:,} is {expected:,}")

    wanted = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", offer.name)))
    shown = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", dialog["text"])))
    confusable = sorted(
        {key for key in (_floor_key(item_name(other))
                         for other in FAVOURITE_SLOTS.values())
         if key != wanted and wanted and wanted in key},
        key=len, reverse=True)
    if any(key in shown for key in confusable):
        halt_buying(
            f"the Confirm Purchase dialog names a different grade than "
            f"{offer.name!r} -- the favourite slots and the shop disagree")
        return False, (f"the dialog names a longer grade than {offer.name!r}: "
                       f"{dialog['text'][:80]!r}")
    if wanted and wanted not in shown:
        halt_buying(f"the Confirm Purchase dialog named something other than "
                    f"{offer.name!r} -- the item mapping cannot be trusted")
        return refuse(f"the dialog does not name {offer.name!r} "
                      f"(it reads {dialog['text'][:70]!r})")

    say(f"  confirming {take} x {offer.name!r} "
        f"({take * offer.pack} item(s)) at {expected:,} Alz")
    bx, by = dialog["buy"]
    move_mouse(bx, by + LAYOUT.length(60))
    time.sleep(0.25)
    click(bx, by)
    time.sleep(2.5)
    park_cursor()
    time.sleep(1.0)

    after = get_alz(grab()) or None
    if before and (not after or before == after):
        deadline = time.monotonic() + ALZ_SETTLE_BUDGET
        while time.monotonic() < deadline:
            time.sleep(ALZ_SETTLE_POLL)
            again = get_alz(grab()) or None
            if again and again != before:
                after = again
                break

    if before and after and before - after == expected:
        record("buy.completed", item=offer.name, price=expected,
               pack=offer.pack * take, took=take)
        note_purchase(offer.name, expected, before - after,
                      offer.pack * take)
        return True, ""
    dialog = purchase_confirm()
    if dialog is not None:
        say("  the dialog would not complete - the listing was taken by "
            "somebody else. Cancelling and searching again.")
        if dialog and dialog["cancel"]:
            click(*dialog["cancel"])
            time.sleep(1.0)
        return False, "sold out before the purchase completed"
    if before and after and before == after:
        return False, ("the listing sold to another buyer before the Buy click "
                       "landed (the game reports 'Item Sold')")

    if not before or not after:
        say("  the Alz balance did not read, so this purchase is recorded at "
            "its listed price rather than a measured one.")
        record("buy.unmeasured", item=offer.name, price=expected,
               pack=offer.pack * take, before=before, after=after)
        note_purchase(offer.name, expected, expected, offer.pack * take,
                      note="unmeasured: the Alz balance did not read")
        return True, ""

    return False, (f"the balance moved {(before or 0) - (after or 0):,}, "
                   f"not the {expected:,} expected")


def buy_cheapest_set_detail(item_slot: int,
                            threshold: "int | None" = None,
                            attempts: int = BUY_RETRY_ATTEMPTS,
                            verbose: bool = True,
                            still_wanted: int | None = None) -> dict:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if threshold is None:
        threshold = price_diff_floor_for(FAVOURITE_SLOTS.get(item_slot, ""))

    def outcome(bought: bool, why: str = "", offer=None, saving=None,
                taken: int = 0) -> dict:
        return {"bought": bought, "why": why, "offer": offer,
                "saving": saving, "slot": item_slot, "taken": taken}

    set_slot = favourite_set_slot(item_slot)
    if set_slot is None:
        say(f"Slot {item_slot} has no paired Set slot; nothing to compare.")
        return outcome(False, "no paired Set slot")

    for attempt in range(1, attempts + 1):
        if attempt > 1:
            say(f"\n=== buy attempt {attempt}/{attempts} ===")

        item_best = _item_price_reusable(item_slot, attempt)
        reused = item_best is not None
        if reused:
            say(f"  loose item {item_best.unit:,.2f}/each (from the read "
                f"moments ago; re-read on any retry)")
        else:
            item_best = cheapest_listing(
                run_favourite_search(item_slot, verbose=verbose))
            note_item_price(item_slot, item_best)
        if item_best is None:
            say("No offers for the loose item, so there is nothing to compare "
                "against - refusing to buy blind.")
            return outcome(False, "no offers for the loose item")

        set_offers = run_favourite_search(set_slot, verbose=verbose)
        set_best = cheapest_listing(set_offers)
        if set_best is None:
            say("No Set offers on screen.")
            return outcome(False, "no Set offers")

        saving = item_best.unit - set_best.unit

        say(f"  item {item_best.unit:>12,.2f}/each   "
            f"set {set_best.unit:>12,.2f}/each   "
            f"saving {saving:>10,.2f}/each")
        if saving < threshold:
            say(f"  saving is under the {threshold:,} threshold"
                + (" (a per-item floor)"
                   if threshold != PRICE_DIFF_FLOOR else "")
                + " - not buying.")
            return outcome(False, f"saving {saving:,.2f} under threshold",
                           saving=saving)

        target = set_best

        target_now = BUY_MAXIMUM or RESTOCK_TARGET
        first_order = (still_wanted is not None
                       and still_wanted >= target_now)
        held = (target_now - still_wanted) if still_wanted is not None else 0
        below_minimum = held < RESTOCK_TARGET
        if (BUY_NEVER_EXCEED_TARGET and still_wanted is not None
                and not below_minimum
                and held + target.pack > BUY_MAXIMUM):
            say(f"  {held} Sets already held; "
                f"row 1 holds {target.pack} and {held + target.pack} would "
                f"pass the {BUY_MAXIMUM} maximum - stopping here.")
            return outcome(False,
                           f"row 1 bundle of {target.pack} would take the "
                           f"total to {held + target.pack}, past the "
                           f"{BUY_MAXIMUM} maximum",
                           saving=saving)
        if (BUY_NEVER_EXCEED_TARGET and still_wanted is not None
                and below_minimum and held + target.pack > BUY_MAXIMUM):
            say(f"  {held} held, under the {RESTOCK_TARGET} minimum: taking "
                f"row 1's bundle of {target.pack} even though "
                f"{held + target.pack} passes the {BUY_MAXIMUM} ceiling - a "
                f"bundle cannot be split and buying is row 1 only, so the "
                f"alternative is buying nothing at all.")
            record("buy.minimum_over_ceiling", held=held, pack=target.pack,
                   total=held + target.pack, maximum=BUY_MAXIMUM,
                   minimum=RESTOCK_TARGET, saving=saving)

        want = max(1, target.available)
        if still_wanted is not None:
            want = min(want, max(1, -(-still_wanted // max(1, target.pack))))
        if not first_order:
            room = max(0, BUY_MAXIMUM - held)
            want = max(1, min(want, max(1, room // max(1, target.pack))))
        else:
            to_floor = max(0, RESTOCK_TARGET - held)
            want = max(1, min(want, max(
                1, -(-to_floor // max(1, target.pack)))))

        order_price = target.price * want
        can_pay = affordable(order_price)
        if can_pay is False:
            held_now = None
            try:
                held_now = get_alz(grab())
            except Exception:
                held_now = None
            fits = (held_now // max(1, target.price)) if held_now else 0
            if fits >= 1 and fits < want:
                say(f"  {order_price:,} Alz is more than the "
                    f"{held_now:,} held - trimming this order from {want} to "
                    f"{fits} row(s) rather than stopping.")
                record("buy.trimmed_to_balance", was=want, now=fits,
                       held=held_now)
                want = fits
                order_price = target.price * want
            else:
                say(f"  {order_price:,} Alz is more than the "
                    f"{held_now if held_now else 0:,} held, and not even one "
                    f"row fits - leaving this restock for a later cycle, when "
                    f"something has sold.")
                record("buy.short_of_alz", want=want, price=order_price,
                       held=held_now or 0)
                return outcome(False, "not enough Alz right now",
                               saving=saving)

        if stop_requested():
            say("")
            say(f"  {STOP_FILE.name} is present - stopping before "
                f"committing any Alz.")
            record("buy.stopped", reason="stop_file")
            return outcome(False, f"{STOP_FILE.name} requested a stop",
                           saving=saving)

        say(f"  buying row {target.row}: {target.name!r} x{want} of "
            f"{target.available} on offer -> {want * target.pack} Sets for "
            f"{order_price:,} ({target.unit:,.2f} each)")
        buy_report: dict = {}
        bought, why = buy_offer(target, want=want, verbose=verbose,
                                report=buy_report)
        if bought:
            asked = want * target.pack
            taken = buy_report.get("items", asked)
            if taken != asked:
                say(f"  the dialog allowed {buy_report.get('take')} of the "
                    f"{want} listing(s) asked for; booking {taken} item(s), "
                    f"not {asked}.")
            say(f"  BOUGHT {taken} x {target.name!r} for {order_price:,} Alz.")
            return outcome(True, offer=target, saving=saving, taken=taken)
        say(f"  not bought: {why}")
        if not is_retryable_refusal(why):
            return outcome(False, why, saving=saving)

    say(f"Gave up after {attempts} attempts - the listings kept selling first.")
    return outcome(False, "the listings kept selling first")


def buy_cheapest_set(item_slot: int,
                     threshold: "int | None" = None,
                     attempts: int = BUY_RETRY_ATTEMPTS,
                     verbose: bool = True) -> bool:
    return buy_cheapest_set_detail(item_slot, threshold=threshold,
                                   attempts=attempts, verbose=verbose)["bought"]


RESTOCK_TARGET = 200

BUY_MAXIMUM = 500
BUY_QTY_GRANULARITY = 250

RESTOCK_MAX_BUYS = 15

BUY_NEVER_EXCEED_TARGET = True

BUY_OVERSHOOT_FACTOR = BUY_MAXIMUM / RESTOCK_TARGET

RESTOCK_MAX_ROUNDS = 40

CORE_SLOT_TRIES = 4

SET_STACK_MAX = 999

RESTOCK_BEFORE_RELIST = True
BUY_NO_SWEEP = False

RESTOCK_MID_CYCLE = True

WAR_START_HOURS = (1, 4, 7, 10, 13, 16, 19, 22)
WAR_MINUTES = 30

WAR_QUIET_BEFORE_END = 60
WAR_QUIET_SECONDS = 300

WAR_STOP_MARGIN = 60.0
WAR_ROW_ALLOWANCE = WAR_STOP_MARGIN
WAR_CHAOS_ALLOWANCE = WAR_STOP_MARGIN

CHAOS_ORDER_SECONDS = 30.0
CHAOS_FIXED_SECONDS = 150.0

WAR_RESTOCK_ALLOWANCE = WAR_STOP_MARGIN


def chaos_row_allowance() -> float:
    return WAR_STOP_MARGIN

WAR_CYCLE_ALLOWANCE = 0.0

SERVER_CLOCK_REGION = (20, 1275, 120, 1310)

SERVER_CLOCK_UNCERTAINTY = 59

SERVER_CLOCK_RESYNC = 1800.0

SERVER_CLOCK_CONFIRM_PAUSE = 1.0
SERVER_CLOCK_MAX_DRIFT = 150.0

SERVER_CLOCK_EPOCH = _dt.datetime(2024, 1, 3)

COST_FLOOR_ON_RELIST = True

BUY_ENABLED = False

BUY_ADDED_ROWS = 0

CORE_STOCK_TTL = 3600.0
_UNLISTED_CACHE: dict | None = None


def note_unlisted(slots: list[int]) -> None:
    global _UNLISTED_CACHE
    _UNLISTED_CACHE = {"slots": list(slots), "at": time.monotonic()}


def forget_unlisted() -> None:
    global _UNLISTED_CACHE
    _UNLISTED_CACHE = None


_CARRIED_SETS: dict[int, int] = {}


def carried_sets(slot: int) -> int:
    return max(0, int(_CARRIED_SETS.get(slot, 0)))


def note_carried_sets(slot: int, count: int) -> None:
    if count > 0:
        _CARRIED_SETS[slot] = int(count)
    else:
        _CARRIED_SETS.pop(slot, None)


def clear_carried(slot: int) -> None:
    _CARRIED_SETS.pop(slot, None)


_CHAOS_STRANDED = False

_RANGE_VIEW: dict = {}


def note_range_view(covered: int, pairs: list) -> None:
    _RANGE_VIEW.clear()
    _RANGE_VIEW.update({"covered": int(covered), "pairs": list(pairs),
                        "at": time.monotonic()})


def forget_range_view() -> None:
    _RANGE_VIEW.clear()


RANGE_VIEW_SECONDS = 120.0

CHAOS_VIEW_REUSE_SECONDS = 30.0


def cached_range_view(need: int,
                      max_age: "float | None" = None) -> "list | None":
    if not _RANGE_VIEW:
        return None
    if _RANGE_VIEW.get("covered", 0) < int(need):
        return None
    limit = RANGE_VIEW_SECONDS if max_age is None else float(max_age)
    if time.monotonic() - _RANGE_VIEW.get("at", 0.0) > limit:
        return None
    return list(_RANGE_VIEW.get("pairs") or [])


def rows_absolute(pairs: list) -> list:
    return [_dc.replace(row, index=absolute) for absolute, row in pairs]


def rows_as_read(pairs: list) -> list:
    return [row for _, row in pairs]


_CHAOS_STRAND_UNIT_COST = 0


def note_chaos_strand(stranded: bool = True, unit_cost: int = 0) -> None:
    global _CHAOS_STRANDED, _CHAOS_STRAND_UNIT_COST
    if stranded and unit_cost > 0:
        _CHAOS_STRAND_UNIT_COST = int(unit_cost)
    elif not stranded:
        _CHAOS_STRAND_UNIT_COST = 0
    _CHAOS_STRANDED = bool(stranded)


def chaos_stranded() -> bool:
    return _CHAOS_STRANDED


def carried_slots() -> list:
    return sorted(list(_CARRIED_SETS),
                  key=lambda s: carried_sets(s), reverse=True)


def carried_total() -> int:
    return sum(carried_sets(slot) for slot in list(_CARRIED_SETS))


def cached_unlisted(missing: list[int]) -> "list[int] | None":
    if _UNLISTED_CACHE is None:
        return None
    if time.monotonic() - _UNLISTED_CACHE["at"] > CORE_STOCK_TTL:
        return None
    known = set(_UNLISTED_CACHE["slots"])
    return [s for s in missing if s in known]


_ROWS_USED_CACHE: dict | None = None


def note_rows_used(count: "int | None") -> None:
    global _ROWS_USED_CACHE
    if count is None or count < 0:
        return
    _ROWS_USED_CACHE = {"rows": int(count), "at": time.monotonic(),
                        "added_at": BUY_ADDED_ROWS}


def forget_rows_used() -> None:
    forget_range_view()
    global _ROWS_USED_CACHE
    _ROWS_USED_CACHE = None


def cached_rows_used() -> "int | None":
    if _ROWS_USED_CACHE is None:
        return None
    since = max(0, BUY_ADDED_ROWS - _ROWS_USED_CACHE.get("added_at", 0))
    return _ROWS_USED_CACHE["rows"] + since


def note_rows_added(count: int) -> None:
    forget_range_view()
    return _note_rows_added(count)


def _note_rows_added(count: int) -> None:
    global BUY_ADDED_ROWS
    if count > 0:
        BUY_ADDED_ROWS += count


def note_shop_depth(rows_now: int, swept_to: int) -> None:
    record("shop_depth.retired", rows_now=rows_now, swept_to=swept_to)


def widen_for_restocks(rows: "list[int] | None",
                       available: int) -> "tuple[list[int], int]":
    if not rows or not BUY_ADDED_ROWS:
        return list(rows or []), 0
    top = max(rows)
    extra = [r for r in range(top + 1, top + 1 + BUY_ADDED_ROWS)
             if r <= available]
    if not extra:
        return list(rows), 0
    return sorted(set(rows) | set(extra)), len(extra)

ENABLE_BUYING: dict[str, bool] = {
    "Force Core(High)":        True,
    "Force Core(Highest)":     True,
    "Force Core (Ultimate)":   True,
    "Upgrade Core (Ultimate)": True,
}


def enabled_buying_slots() -> tuple[int, ...]:
    out = []
    for name, on in ENABLE_BUYING.items():
        slot = favourite_for(name)
        if slot is None or slot not in managed_core_slots():
            raise ValueError(
                f"ENABLE_BUYING names {name!r}, which is not a managed Core. "
                "Managed: "
                + ", ".join(FAVOURITE_SLOTS[s] for s in managed_core_slots()))
        if on:
            out.append(slot)
    return tuple(sorted(out))


BUY_HALTED = False
BUY_HALT_REASON = ""


def halt_buying(reason: str) -> None:
    global BUY_HALTED, BUY_HALT_REASON
    if not BUY_HALTED:
        BUY_HALTED = True
        BUY_HALT_REASON = reason
        print(f"\nBUYING HALTED for the rest of this run: {reason}")
        print("Relisting continues, untouched.")
        if "afford" in reason:
            print("Restart once something has sold and there is Alz again.")
        else:
            print("Check FAVOURITE_SLOTS against the game's saved searches "
                  "before restarting: a name mismatch means the script and "
                  "the shop disagree about what is being bought.")
        record("buy.halted", reason=reason)


def affordable(price: int, source: "Image.Image | None" = None) -> bool | None:
    try:
        held = get_alz(source if source is not None else grab())
    except Exception:
        return None
    if not held:
        return None
    return held >= price


def leave_for_restock(verbose: bool = True) -> None:
    try:
        if PREMIUM_ENABLED:
            if verbose:
                print("  leaving the Agent Shop OPEN for the refill "
                      "(--premium), but scrolling the table back to the top so "
                      "the Purchase tab is where it is expected.")
            try:
                if trade_window_open():
                    scroll_to_end(up=True, verbose=False)
            except Exception as exc:
                if verbose:
                    print(f"  (could not scroll the table back: {exc})")
            return
        if trade_window_open():
            leave_shop(verbose=verbose)
    except Exception as exc:
        if verbose:
            print(f"  (could not close the Agent Shop first: {exc})")


DISCONNECT_REGION = (980, 545, 1590, 860)


def game_disconnected(source: "Image.Image | None" = None) -> bool:
    try:
        shot = source if source is not None else grab()
        words = [w.text.casefold()
                 for w in find_words(shot, DISCONNECT_REGION, 20)
                 if w.conf >= 60]
    except Exception:
        return False
    text = " ".join(words)
    return "disconnected" in text and "server" in text


def restock_is_armed() -> bool:
    if BUY_HALTED:
        return False
    return bool(BUY_ENABLED) and bool(enabled_buying_slots())


def managed_core_slots() -> list[int]:
    out = []
    for slot, name in sorted(FAVOURITE_SLOTS.items()):
        if slot in CHAOS_SLOTS:
            continue
        if "set" in _floor_key(item_name(name)):
            continue
        if favourite_set_slot(slot) is not None:
            out.append(slot)
    return out


def core_row_counts(listings: list) -> dict[int, int]:
    counts = {slot: 0 for slot in managed_core_slots()}
    for row in listings:
        if getattr(row, "action", None) == "receive":
            continue
        name = _PACK_ANYWHERE.sub(" ", getattr(row, "name", None) or "")
        for slot in counts:
            if _names_agree(name, FAVOURITE_SLOTS[slot]):
                counts[slot] += 1
    return counts


RESTOCK_AT_OR_BELOW_ROWS = 1

RESTOCK_PRIORITY_NAMES = (
    "Force Core(High)",
    "Upgrade Core (Ultimate)",
    "Force Core (Ultimate)",
    "Force Core(Highest)",
)


def restock_rank(slot: int) -> tuple:
    name = _floor_key(item_name(FAVOURITE_SLOTS.get(slot, "")))
    for i, want in enumerate(RESTOCK_PRIORITY_NAMES):
        if name == _floor_key(item_name(want)):
            return (0, i, slot)
    return (1, 0, slot)


def in_restock_priority(slots) -> list:
    return sorted(slots, key=restock_rank)


def slots_needing_restock(listings: list) -> list[int]:
    return in_restock_priority(
        slot for slot, n in core_row_counts(listings).items()
        if n <= RESTOCK_AT_OR_BELOW_ROWS)


VENDOR_TAB_REGION = (0, 150, 620, 240)
VENDOR_TABS = ("Normal", "Dungeon", "Repurchase")
CONVERT_VENDOR_TAB = "Dungeon"
VENDOR_TAB_BAND = (176, 188)
VENDOR_TAB_HALF_W = 25
VENDOR_TAB_REFS = {"Dungeon": (185, 175)}
VENDOR_TAB_MARGIN = 12.0


def vendor_tab_point(name: str,
                     source: "Image.Image | None" = None) -> "tuple[int, int] | None":
    shot = source if source is not None else grab()
    hits = find_text(shot, name, VENDOR_TAB_REGION, 40.0)
    if not hits:
        return None
    if name not in VENDOR_TAB_REFS:
        record("vendor_tab.located", tab=name, centre=str(hits[0].centre))
    return hits[0].centre


def active_vendor_tab(source: "Image.Image | None" = None) -> "str | None":
    shot = source if source is not None else grab()
    grey = shot.convert("L")
    px = grey.load()
    top, bottom = VENDOR_TAB_BAND
    scores = []
    for name in VENDOR_TABS:
        point = vendor_tab_point(name, shot)
        if point is None:
            continue
        cx = point[0]
        vals = [px[x, y]
                for x in range(max(0, cx - VENDOR_TAB_HALF_W),
                               min(grey.width, cx + VENDOR_TAB_HALF_W))
                for y in range(top, bottom)]
        scores.append((sum(vals) / len(vals), name))
    if len(scores) < 2:
        return None
    scores.sort(reverse=True)
    if scores[0][0] - scores[1][0] < VENDOR_TAB_MARGIN:
        return None
    return scores[0][1]


def open_vendor_tab(name: str = CONVERT_VENDOR_TAB, timeout: float = 8.0,
                    verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    ref = VENDOR_TAB_REFS.get(name)
    point = LAYOUT.point(ref) if (ref and layout_is_fitted()) else None
    if point is not None and not vendor_shop_open():
        point = None
    deadline = time.monotonic() + timeout
    while point is None and time.monotonic() < deadline:
        point = vendor_tab_point(name)
        if point is not None:
            break
        time.sleep(0.4)
    if point is None:
        say(f"  could not find the {name!r} tab in the vendor Shop after "
            f"{timeout:g}s.")
        seen = [t for t in VENDOR_TABS if vendor_tab_point(t) is not None]
        say(f"  tabs that DID read: {seen or 'none'} -- if none, the window "
            f"was still drawing or is not the vendor Shop; if some, only this "
            f"label failed to OCR.")
        return False
    say(f"  selecting the {name} tab at {point}")
    click(*point)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_vendor_tab() == name:
            say(f"  {name} tab is showing.")
            return True
        time.sleep(0.4)
    say(f"  the {name} tab did not take.")
    return False


def open_npc_shop(timeout: float = 10.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if vendor_shop_open():
        if trade_window_open():
            say("The vendor Shop is already open, but so is the Agent Shop - "
                "closing that first; it covers the grid.")
            leave_shop(verbose=verbose)
            if trade_window_open():
                say("  the Agent Shop would not close; refusing to convert "
                    "underneath it.")
                return False
        return open_vendor_tab(verbose=verbose)

    if trade_window_open():
        say("Closing the Agent Shop before opening the vendor...")
        leave_shop(verbose=verbose)
        if trade_window_open():
            say("  the Agent Shop would not close; not opening the vendor on "
                "top of it.")
            return False

    say("Opening the vendor Shop (N)...")
    press_key(VK_N, settle=0.6, what="N")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if vendor_shop_open():
            say("  vendor Shop is open.")
            return open_vendor_tab(verbose=verbose)
        time.sleep(0.4)
    say("  the vendor Shop did not open.")
    return False


def close_npc_shop(verbose: bool = True) -> bool:
    for _ in range(ESCAPE_ATTEMPTS):
        if not vendor_shop_open():
            return True
        press_escape()
    closed = not vendor_shop_open()
    if not closed and verbose:
        print("  the vendor Shop would not close.")
    return closed


SHOP_ROW_CAPACITY = 30


def shop_listing_pairs(timeout: float = 8.0,
                       verbose: bool = True,
                       stop_after: "int | None" = None) -> list | None:
    if not register_tab_open() and not open_trade_window(
            timeout=max(timeout, 15.0), verbose=verbose):
        return None
    return enumerate_listings(timeout=timeout, verbose=verbose,
                              stop_after=stop_after)


def whole_shop_listings(timeout: float = 8.0,
                        verbose: bool = True,
                        stop_after: "int | None" = None) -> list | None:
    if not register_tab_open() and not open_trade_window(
            timeout=max(timeout, 15.0), verbose=verbose):
        return None
    listings = enumerate_listings(timeout=timeout, verbose=verbose,
                                  stop_after=stop_after)
    if listings is None:
        return None
    return [row for _, row in listings]


def shop_rows_used(timeout: float = 8.0,
                   verbose: bool = True) -> int | None:
    if not register_tab_open() and not open_trade_window(
            timeout=max(timeout, 15.0), verbose=verbose):
        return None
    listings = enumerate_listings(timeout=timeout, verbose=verbose)
    if listings is None:
        return None
    used = sum(1 for _, row in listings
               if getattr(row, "action", None) in ("change", "receive"))
    note_rows_used(used)
    return used


def restock_rows_needed(target: int = RESTOCK_TARGET) -> int:
    worst = max(RESTOCK_TARGET - 1 + SET_STACK_MAX, BUY_MAXIMUM)
    return max(1, -(-worst // max(1, CONVERT_QUANTITY)))


def open_purchase_tab(timeout: float = 10.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if purchase_tab_open():
        if set_purchase_sort_low_to_high(verbose=verbose):
            return True
        say("  the tab read as Purchase but the sort would not set; "
            "clicking the tab rather than trusting that read.")

    if not trade_window_open():
        say("  the Trade window is shut; opening it first.")
        if not open_trade_window(timeout=max(timeout, 15.0), verbose=verbose):
            return False
        if purchase_tab_open():
            if set_purchase_sort_low_to_high(verbose=verbose):
                return True
            say("  the tab read as Purchase after opening the window but the "
                "sort would not set; clicking the tab.")

    if LAYOUT and "reference defaults" not in (LAYOUT.measured_from or ""):
        label = LAYOUT.point(PURCHASE_TAB_REF)
    else:
        label = find_phrase(grab(), PURCHASE_TAB_WORD, TRADE_WINDOW_SEARCH)
        if label is None:
            say(f"  the window is not calibrated and the "
                f"{PURCHASE_TAB_WORD!r} tab could not be read.")
            return False

    say(f"  switching to the {PURCHASE_TAB_WORD} tab at {label}")
    click(*label)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if purchase_tab_open():
            say("  Purchase tab is open.")
            return set_purchase_sort_low_to_high(verbose=verbose)
        time.sleep(0.4)
    say("  the Purchase tab did not open.")
    return False


ITEM_PRICE_REUSE_SECONDS = 180.0
_ITEM_PRICE_CACHE: dict = {}


def note_item_price(slot: int, offer) -> None:
    if offer is not None:
        _ITEM_PRICE_CACHE[slot] = {"offer": offer, "at": time.monotonic()}


def forget_item_prices() -> None:
    _ITEM_PRICE_CACHE.clear()


def _item_price_reusable(slot: int, attempt: int):
    if attempt != 1:
        return None
    entry = _ITEM_PRICE_CACHE.get(slot)
    if entry is None:
        return None
    if time.monotonic() - entry["at"] > ITEM_PRICE_REUSE_SECONDS:
        return None
    return entry["offer"]


def buy_sets_until(item_slot: int,
                   target: int = RESTOCK_TARGET,
                   max_buys: int = RESTOCK_MAX_BUYS,
                   threshold: "int | None" = None,
                   verbose: bool = True) -> dict:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if threshold is None:
        threshold = price_diff_floor_for(FAVOURITE_SLOTS.get(item_slot, ""))

    forget_item_prices()

    bought = 0
    orders = []
    for attempt in range(1, max_buys + 1):
        if bought >= target:
            break
        if BUY_HALTED:
            say(f"  buying is halted: {BUY_HALT_REASON}")
            break
        say(f"\n-- buy {attempt}/{max_buys}: {bought}/{target} Sets held --")
        result = buy_cheapest_set_detail(item_slot, threshold=threshold,
                                         verbose=verbose,
                                         still_wanted=target - bought)
        if not result["bought"]:
            say(f"  stopping: {result['why']}")
            break
        offer = result["offer"]
        got = int(result.get("taken") or offer.pack)
        bought += got
        orders.append(offer)
        say(f"  +{got} Sets ({bought}/{target})")

    if bought < target:
        say(f"  bought {bought} of the {target} wanted "
            f"({len(orders)} order(s)).")
    else:
        say(f"  target met: {bought} Sets in {len(orders)} order(s).")
    return {"slot": item_slot, "bought": bought, "target": target,
            "orders": orders}


def list_cores(core_name: str, slots, timeout: float = 8.0,
               verbose: bool = True, expect_rows: int | None = None) -> dict:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if isinstance(slots, tuple) and len(slots) == 2 and isinstance(slots[0], int):
        slots = [slots]
    slots = list(slots or [])
    if not slots:
        return {"ok": False, "qty": 0, "why": "no candidate slot to list from"}

    if not open_trade_window(timeout=max(timeout, 15.0), verbose=verbose):
        say("  could not open the Agent Shop to list the Cores.")
        return {"ok": False, "qty": 0, "why": "the Agent Shop would not open"}

    inv = inventory_origin()
    if inv is None or not select_inventory_tab(WORK_TAB, inv):
        say(f"  could not put the Inventory on tab {WORK_TAB}, which is where "
            f"the converted Cores are -- refusing to click slot numbers on "
            f"whatever tab happens to be showing.")
        return {"ok": False, "qty": 0,
                "why": f"could not reach inventory tab {WORK_TAB}"}

    why = ""
    tried_any = False
    for attempt, slot in enumerate(slots[:CORE_SLOT_TRIES], start=1):
        tried_any = True
        say(f"Listing {core_name} from inventory slot {slot} "
            f"(candidate {attempt}/{min(len(slots), CORE_SLOT_TRIES)})...")
        report: dict = {}
        try:
            ok = register_item(*slot, timeout=timeout, verbose=verbose,
                               maximise_qty=True, expect_item=core_name,
                               report=report)
        except Aborted as exc:
            why = str(exc)
            say(f"  slot {slot} was refused: {exc}")
            continue
        qty = int(report.get("qty") or 0)
        if ok:
            say(f"  listed {qty} x {core_name}.")
            verified = sanity_check(core_name, report.get("price"), qty,
                                    timeout=timeout, verbose=verbose,
                                    expect_at_least=expect_rows)
            if not verified:
                say(f"WARNING: {core_name!r} was registered from slot {slot} "
                    f"but the shop table does not show it at "
                    f"{report.get('price')} for {qty}. The slot may have held "
                    f"something else. CHECK THE SHOP.")
                record("list_cores.unverified", item=core_name, slot=str(slot),
                       price=report.get("price"), qty=qty)
            return {"ok": True, "qty": qty, "why": "", "slot": slot,
                    "verified": verified}
        why = "the registration did not complete"
    say(f"  not on tab {CONVERT_INVENTORY_TAB}; looking on the other tabs.")
    origin = inventory_origin()
    if origin is None:
        return {"ok": False, "qty": 0,
                "why": why or "the Inventory panel is not open"}
    for tab in range(1, TAB_COUNT + 1):
        if tab == CONVERT_INVENTORY_TAB:
            continue
        if not select_inventory_tab(tab, origin):
            continue
        here = occupied_slots(grab(), origin)
        if not here:
            continue
        for slot in here[:CORE_SLOT_TRIES]:
            report = {}
            try:
                ok = register_item(*slot, timeout=timeout, verbose=verbose,
                                   maximise_qty=True, expect_item=core_name,
                                   report=report)
            except Aborted as exc:
                why = str(exc)
                continue
            if ok:
                qty = int(report.get("qty") or 0)
                say(f"  listed {qty} x {core_name} from tab {tab} slot {slot}.")
                verified = sanity_check(core_name, report.get("price"), qty,
                                        timeout=timeout, verbose=verbose,
                                        expect_at_least=expect_rows)
                if not verified:
                    say(f"WARNING: {core_name!r} was registered from tab {tab} "
                        f"slot {slot} but the shop table does not show it at "
                        f"{report.get('price')} for {qty}. CHECK THE SHOP.")
                    record("list_cores.unverified", item=core_name,
                           slot=str(slot), tab=tab,
                           price=report.get("price"), qty=qty)
                return {"ok": True, "qty": qty, "why": "", "slot": slot,
                        "tab": tab, "verified": verified}
    return {"ok": False, "qty": 0,
            "why": why or ("no slot on any tab held the Cores"
                           if tried_any else "no candidate slot to list from")}


def restock_core(item_slot: int,
                 target: int = RESTOCK_TARGET,
                 max_rounds: int = RESTOCK_MAX_ROUNDS,
                 verbose: bool = True,
                 rows_used: int | None = None,
                 scope: "list[int] | None" = None,
                 rows_in_scope_used: int = 0,
                 chaos_rows_in_scope: int = 0,
                 ceiling: "int | None" = None) -> dict:
    def say(message: str) -> None:
        if verbose:
            print(message)

    core = FAVOURITE_SLOTS.get(item_slot, "")
    result = {"slot": item_slot, "core": core, "bought": 0,
              "converted": 0, "listed": 0, "rounds": 0,
              "rows_listed": 0, "rows_grown": 0, "why": ""}
    if not core or convert_cell_for(core) is None:
        result["why"] = f"slot {item_slot} ({core!r}) is not a convertible Core"
        say(f"  {result['why']}")
        return result

    say(f"\n{'=' * 70}\nRESTOCK {core} (favourite slot {item_slot})\n{'=' * 70}")

    used = rows_used if rows_used is not None else shop_rows_used(verbose=False)
    resuming = carried_sets(item_slot)
    if resuming > 0:
        need = 1
    else:
        need = restock_rows_needed(target)
    if used is None:
        result["why"] = ("could not count the shop's rows, so there is no way "
                         "to know whether the result would fit")
        say(f"  {result['why']}")
        return result
    room = SHOP_ROW_CAPACITY
    where = f"{used}/{SHOP_ROW_CAPACITY} rows"
    if scope:
        inside = sorted(set(scope))
        free_inside = max(0, len(inside) - rows_in_scope_used)
        held = 0
        if CHAOS_ENABLED:
            cap = max(0, len(inside) - CHAOS_ROWS)
            non_chaos_used = max(0, rows_in_scope_used - chaos_rows_in_scope)
            free_for_cores = max(0, cap - non_chaos_used)
            held = max(0, free_inside - free_for_cores)
            free_inside = min(free_inside, free_for_cores)
        room = used + free_inside
        where = (f"{rows_in_scope_used}/{len(inside)} rows inside "
                 f"{min(inside)}-{max(inside)}"
                 + (f" ({held} held so chaos can always fit "
                    f"{CHAOS_ROWS})" if held else ""))
    if used + need > room:
        result["why"] = (f"paused: {where} used and a restock needs up to "
                         f"{need} more. It will NOT list outside the range, "
                         f"because a row out there is never repriced again.")
        say(f"  {result['why']}")
        return result
    say(f"  shop has {used}/{SHOP_ROW_CAPACITY} rows used; a restock needs up "
        f"to {need} more")

    carried = carried_sets(item_slot)
    if carried > 0:
        say(f"  {carried} Set(s) bought by an earlier restock are still in "
            "the bag and unlisted -- converting and listing those rather "
            "than buying more.")
        purchase = {"bought": carried}
        result["bought"] = carried
        result["resumed"] = True
    else:
        if not open_purchase_tab(verbose=verbose):
            result["why"] = "could not reach the Purchase tab to buy"
            say(f"  {result['why']}")
            return result

        inv = inventory_origin()
        if inv is None:
            result["why"] = ("the Inventory panel is not open, so there is "
                             "nowhere known to buy onto")
            say(f"  {result['why']}")
            return result
        if not select_inventory_tab(CONVERT_INVENTORY_TAB, inv):
            result["why"] = (f"could not put the Inventory on tab "
                             f"{CONVERT_INVENTORY_TAB} before buying")
            say(f"  {result['why']}")
            return result
        say(f"  buying onto inventory tab {CONVERT_INVENTORY_TAB}")

        stop_at = BUY_MAXIMUM if ceiling is None else ceiling
        purchase = buy_sets_until(item_slot, target=stop_at, verbose=verbose)
        result["bought"] = purchase["bought"]
        if purchase["bought"] <= 0:
            result["why"] = "no Sets were bought"
            return result

        note_carried_sets(item_slot, purchase["bought"])

    inv = inventory_origin()
    if inv is not None:
        select_inventory_tab(CONVERT_INVENTORY_TAB, inv)

    say(f"  {purchase['bought']} Sets to convert, up to {CONVERT_QUANTITY} a "
        f"round, until they are all listed")
    for rnd in range(1, max_rounds + 1):
        result["rounds"] = rnd
        say(f"\n-- round {rnd}: convert then list --")
        if not open_npc_shop(verbose=verbose):
            result["why"] = "could not open the vendor Shop"
            break
        try:
            conv = convert_cores(core, quantity=CONVERT_QUANTITY,
                                 verbose=verbose, require_layout=False)
        except Aborted as exc:
            result["why"] = f"conversion stopped: {exc}"
            say(f"  {result['why']}")
            break
        result["converted"] += conv["converted"]
        candidates = conv.get("candidates") or []

        if conv["converted"] <= 0 and not candidates:
            result["why"] = "nothing converted this round"
            break

        close_npc_shop(verbose=verbose)
        listing = list_cores(core, candidates, verbose=verbose)
        if not listing["ok"]:
            if conv["converted"] <= 0:
                result["why"] = "nothing converted this round"
            else:
                result["why"] = ("the converted Cores could not be listed: "
                                 f"{listing['why']}")
            break
        if listing.get("verified", True):
            result["listed"] += listing["qty"]
        else:
            say(f"  NOT counting {listing['qty']} toward the target: the "
                f"listing could not be verified on the board, so the Sets "
                f"stay on the books rather than being written off.")
            record("restock.unverified_not_counted", slot=item_slot,
                   qty=listing["qty"])
        result["rows_listed"] += 1
        say(f"  round {rnd}: converted {conv['converted']}, listed "
            f"{listing['qty']} -- {result['listed']}/{purchase['bought']} done")

        if result["listed"] >= purchase["bought"]:
            break

    close_npc_shop(verbose=False)

    if result["rows_listed"]:
        result["rows_grown"] = result["rows_listed"]
        say(f"  shop went {used} -> {used + result['rows_grown']} rows "
            f"({result['rows_listed']} listing(s), each occupying one row)")

    outstanding = max(0, result["bought"] - result["listed"])
    note_carried_sets(item_slot, outstanding)
    if outstanding and not result["why"]:
        say(f"  {outstanding} Set(s) remain in the bag; the next restock pass "
            "will convert those rather than buying more.")

    if result["listed"] < result["bought"] and not result["why"]:
        result["why"] = (f"stopped at the {max_rounds}-round guard with "
                         f"{result['bought'] - result['listed']} Set(s) still "
                         "unconverted -- they are in the inventory, not lost")
    say(f"\n{core}: bought {result['bought']}, converted "
        f"{result['converted']}, listed {result['listed']}"
        + (f" -- {result['why']}" if result["why"] else ""))
    return result


def rows_in_use(listings: list) -> int:
    return sum(1 for row in listings
               if getattr(row, "action", None) in ("change", "receive"))


def restock_sold_out_slots(slots: list[int], verbose: bool = True,
                           rows_used: "int | None" = None,
                           scope: "list[int] | None" = None,
                           rows_in_scope_used: int = 0,
                           chaos_rows_in_scope: int = 0) -> list[dict]:
    allowed = enabled_buying_slots()
    wanted = [s for s in slots if s in allowed]
    if not wanted:
        return []
    return _restock_each(wanted, rows_used=rows_used, verbose=verbose,
                         scope=scope, rows_in_scope_used=rows_in_scope_used,
                         chaos_rows_in_scope=chaos_rows_in_scope)


def restock_sold_out(listings: list, verbose: bool = True) -> list[dict]:
    def say(message: str) -> None:
        if verbose:
            print(message)

    allowed = enabled_buying_slots()
    counts = core_row_counts(listings)
    low = slots_needing_restock(listings)
    slots = [s for s in low if s in allowed]
    ignored = [s for s in low if s not in allowed]

    def describe(slot: int) -> str:
        n = counts.get(slot, 0)
        return (f"{FAVOURITE_SLOTS[slot]} (sold out)" if n == 0
                else f"{FAVOURITE_SLOTS[slot]} ({n} row(s) left)")

    if ignored:
        say("Low or sold out, but buying is OFF for: "
            + ", ".join(describe(s) for s in ignored))
    if not slots:
        return []
    say(f"\nRestocking at or below {RESTOCK_AT_OR_BELOW_ROWS} row(s): "
        + ", ".join(describe(s) for s in slots))
    return _restock_each(slots, rows_used=rows_in_use(listings),
                         verbose=verbose)


def _restock_each(slots: list[int], rows_used: int | None,
                  verbose: bool = True,
                  scope: "list[int] | None" = None,
                  rows_in_scope_used: int = 0,
                  chaos_rows_in_scope: int = 0) -> list[dict]:
    def say(message: str) -> None:
        if verbose:
            print(message)

    done = []
    shop_grew = 0
    for slot in slots:
        if stop_requested():
            say(f"  {STOP_FILE.name} is present - stopping with "
                f"{len(slots) - slots.index(slot)} Core(s) not restocked.")
            record("restock.stopped", reason="stop_file", where="between_slots")
            break
        avoid_warlag(allowance=WAR_RESTOCK_ALLOWANCE, verbose=verbose)
        outcome = restock_core(
            slot, target=BUY_MAXIMUM, verbose=verbose,
            rows_used=None if rows_used is None else rows_used + shop_grew,
            scope=scope, rows_in_scope_used=rows_in_scope_used + shop_grew,
            chaos_rows_in_scope=chaos_rows_in_scope)
        done.append(outcome)
        if int(outcome.get("rows_listed") or 0):
            forget_unlisted()
        grew = int(outcome.get("rows_grown") or 0)
        shop_grew += grew
        if grew:
            note_rows_added(grew)
            say(f"  the shop grew by {grew} row(s); sweeps now widen by "
                f"{BUY_ADDED_ROWS}")
    return done


WORK_TAB = 4
STRAND_RECOVERY_ATTEMPTS = 3

STOP_FILE = Path(__file__).resolve().with_name("STOP")


def stop_requested() -> bool:
    try:
        return STOP_FILE.exists()
    except OSError:
        return False


LIVE_CONFIG_FILE = Path(__file__).resolve().with_name("config.json")

LIVE_KNOBS = {
    "CHAOS_ENABLED":                  (bool, "run the chaos pass at all"),
    "CHAOS_ROWS":                     (int, "bundles to keep on the board"),
    "CHAOS_RESTOCK_AT_OR_BELOW_ROWS": (int, "rebuy at or below this many"),
    "CHAOS_BUY_QUANTITY":             (int, "Cores per top-up"),
    "CHAOS_MARGIN_FLOOR":             (int, "min Set-over-Core spread per unit"),
    "CHAOS_UNDERCUT":                 (int, "Alz shaved off a chaos listing"),
    "CHAOS_RECIPE":                   (int, "which craft recipe chaos uses: "
                                            "1 = [1500] Chaos Core Set (x1), "
                                            "2 = [2500] Chaos Core Set (x3)"),
    "BUY_ENABLED":                    (bool, "restock the ordinary Cores"),
    "RESTOCK_TARGET":                 (int, "core min: hard floor per restock"),
    "BUY_MAXIMUM":                    (int, "core max: soft ceiling, and "
                                             "what a restock runs to"),
    "RESTOCK_AT_OR_BELOW_ROWS":       (int, "restock a Core at/below this many rows"),
    "SHOP_MODEL_SHADOW":              (bool, "track the row model without acting"),
    "COST_FLOOR_ON_RELIST":           (bool, "never relist below what the stock cost"),
}


RUN_KNOBS = {
    "relist_rows":   (str, "rows this batch manages, e.g. \"1-16\""),
    "for_minutes":   (int, "how long to keep looping"),
    "every_minutes": (int, "gap between cycle STARTS; 0 = back to back"),
    "premium":       (bool, "open the shop by the inventory key, not the NPC"),
    "debug_frames":  (bool, "a screenshot after every input (slow)"),
    "row_model":     (bool, "TRUST the 30-slot model: one seeding walk, then "
                            "no discovery sweeps, and a disagreeing row ends "
                            "the run instead of resyncing"),
}


def _run_shape_problems(run: dict) -> "list[str]":
    bad = []
    for name, want in run.items():
        kind = RUN_KNOBS[name][0]
        if kind is bool and not isinstance(want, bool):
            bad.append(f"run.{name} must be true or false, got {want!r}")
        elif kind is int and (isinstance(want, bool)
                              or not isinstance(want, int)):
            bad.append(f"run.{name} must be a whole number, got {want!r}")
        elif kind is str and not isinstance(want, str):
            bad.append(f"run.{name} must be text, got {want!r}")
    if bad:
        return bad
    if "relist_rows" in run:
        try:
            if not parse_row_spec([run["relist_rows"]]):
                bad.append(f"run.relist_rows {run['relist_rows']!r} names no "
                           f"rows")
        except Exception as exc:
            bad.append(f"run.relist_rows {run['relist_rows']!r} is not a row "
                       f"spec ({exc})")
    if run.get("for_minutes", 1) <= 0:
        bad.append("run.for_minutes must be positive")
    if run.get("every_minutes", 0) < 0:
        bad.append("run.every_minutes cannot be negative")
    return bad


def _live_config_problems(values):
    bad = []
    for name, want in values.items():
        kind = LIVE_KNOBS[name][0]
        if kind is bool and not isinstance(want, bool):
            bad.append(f"{name} must be true or false, got {want!r}")
        elif kind is int and (isinstance(want, bool)
                              or not isinstance(want, int)):
            bad.append(f"{name} must be a whole number, got {want!r}")
        elif kind is int and want < 0:
            bad.append(f"{name} cannot be negative, got {want}")
    if bad:
        return bad

    def val(name):
        return values.get(name, globals()[name])

    if val("CHAOS_ROWS") > SHOP_ROW_CAPACITY:
        bad.append(f"CHAOS_ROWS {val('CHAOS_ROWS')} is more than the "
                   f"{SHOP_ROW_CAPACITY}-row shop")
    if val("CHAOS_RESTOCK_AT_OR_BELOW_ROWS") > val("CHAOS_ROWS"):
        bad.append(f"CHAOS_RESTOCK_AT_OR_BELOW_ROWS "
                   f"{val('CHAOS_RESTOCK_AT_OR_BELOW_ROWS')} is above "
                   f"CHAOS_ROWS {val('CHAOS_ROWS')}: every cycle would "
                   f"restock")
    if val("RESTOCK_TARGET") > val("BUY_MAXIMUM"):
        bad.append(f"RESTOCK_TARGET {val('RESTOCK_TARGET')} is above "
                   f"BUY_MAXIMUM {val('BUY_MAXIMUM')}: the hard floor cannot "
                   f"exceed the soft ceiling")
    if val("CHAOS_BUY_QUANTITY") < 1:
        bad.append("CHAOS_BUY_QUANTITY must be at least 1")
    if val("RESTOCK_TARGET") < 1:
        bad.append("RESTOCK_TARGET must be at least 1: 0 removes the floor "
                   "that bounds the first order of a restock")
    if val("CHAOS_ROWS") < 1:
        bad.append("CHAOS_ROWS must be at least 1")
    if val("RESTOCK_AT_OR_BELOW_ROWS") > SHOP_ROW_CAPACITY:
        bad.append(f"RESTOCK_AT_OR_BELOW_ROWS "
                   f"{val('RESTOCK_AT_OR_BELOW_ROWS')} is past the "
                   f"{SHOP_ROW_CAPACITY}-row shop: every Core would restock "
                   f"every cycle")
    return bad


def export_live_config(path=None, verbose=True, run_shape=None):
    run_shape = run_shape or {}
    target = Path(path) if path else LIVE_CONFIG_FILE
    body = {
        "_README": [
            "Edit this while the script runs. It is re-read at the top of "
            "every cycle, so a change takes effect on the NEXT cycle and "
            "never mid-action.",
            "A malformed or invalid file is IGNORED with a warning: the run "
            "keeps the values it already has rather than stopping.",
            "Changes are applied ALL OR NOTHING, because these knobs "
            "constrain each other.",
            "Row scope, --for and --every are not here - the batch and the "
            "loop are built around them and they are not safe to change "
            "mid-run.",
        ],
        "_NOTES": {n: LIVE_KNOBS[n][1] for n in LIVE_KNOBS},
    }
    body.update({n: globals()[n] for n in LIVE_KNOBS})
    body["run"] = {
        "relist_rows": run_shape.get("relist_rows", "1-10"),
        "for_minutes": run_shape.get("for_minutes", 600),
        "every_minutes": run_shape.get("every_minutes", 0),
        "premium": run_shape.get("premium", False),
        "debug_frames": run_shape.get("debug_frames", False),
        "row_model": run_shape.get("row_model", False),
    }
    if target.exists():
        if verbose:
            print(f"Reading {target.name}; it is the source of truth and is "
                  f"not overwritten by this run.")
        return target
    try:
        target.write_text(json.dumps(body, indent=2), encoding="utf-8")
    except OSError as exc:
        if verbose:
            print(f"Could not write {target.name}: {exc}")
        return None
    if verbose:
        print(f"Live config written to {target.name} - edit it while the run "
              f"is going and the next cycle picks it up.")
    record("config.exported", path=str(target))
    return target


def read_run_shape(verbose: bool = True) -> "dict | None":
    def say(message):
        if verbose:
            print(message)

    try:
        raw = json.loads(LIVE_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        say(f"{LIVE_CONFIG_FILE.name} could not be read ({exc}).")
        return None
    if not isinstance(raw, dict):
        return None
    run = {k: v for k, v in (raw.get("run") or {}).items() if k in RUN_KNOBS}
    if not run:
        return None
    problems = _run_shape_problems(run)
    if problems:
        say(f"{LIVE_CONFIG_FILE.name} run block is unusable:")
        for why in problems:
            say(f"  - {why}")
        record("config.run_rejected", problems=problems)
        return None
    return run


def apply_live_config(verbose=True):
    def say(message):
        if verbose:
            print(message)

    try:
        raw = json.loads(LIVE_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        say(f"  {LIVE_CONFIG_FILE.name} could not be read ({exc}); keeping "
            f"the values already in use.")
        record("config.unreadable", why=str(exc))
        return []

    if not isinstance(raw, dict):
        say(f"  {LIVE_CONFIG_FILE.name} is not an object; ignoring it.")
        return []

    wanted = {n: raw[n] for n in LIVE_KNOBS if n in raw}
    changed = {n: v for n, v in wanted.items() if globals()[n] != v}
    if not changed:
        return []

    problems = _live_config_problems(wanted)
    if problems:
        say(f"  {LIVE_CONFIG_FILE.name} was NOT applied:")
        for why in problems:
            say(f"    - {why}")
        record("config.rejected", problems=problems)
        return []

    for name, value in changed.items():
        globals()[name] = value
    names = sorted(changed)
    say(f"  {LIVE_CONFIG_FILE.name} changed: "
        + ", ".join(f"{n}={globals()[n]!r}" for n in names))
    record("config.applied", changed={n: changed[n] for n in names})
    return names

EXPECTED_ROWS = 10
assert SCREEN_ROWS == EXPECTED_ROWS, (
    f"SCREEN_ROWS ({SCREEN_ROWS}) must equal EXPECTED_ROWS "
    f"({EXPECTED_ROWS}): both mean one screen of the listings table")
assert CHAOS_ROWS <= SHOP_ROW_CAPACITY, (
    f"CHAOS_ROWS ({CHAOS_ROWS}) is more than the {SHOP_ROW_CAPACITY}-row "
    f"shop")


RECORD_ENABLED = False

_OCR_STATS: dict = {}
OCR_PROFILE = False

_FRAME_SERIAL = 0
_OCR_CACHE: dict = {}
OCR_CACHE_ENABLED = True
OCR_BACKEND = None

_OCR_ENGINES: dict = {}


def _load_rapidocr():
    from rapidocr_onnxruntime import RapidOCR
    engine = RapidOCR()

    def read(image, min_conf=0.0):
        import numpy as _np
        result, _ = engine(_np.array(image.convert("RGB")))
        out = []
        for box, text, score in (result or []):
            conf = float(score) * 100.0
            if conf < min_conf:
                continue
            xs = [pt[0] for pt in box]
            ys = [pt[1] for pt in box]
            out.append((str(text), int(min(xs)), int(min(ys)),
                        int(max(xs)), int(max(ys)), conf))
        return out
    return read


def _load_paddleocr():
    from paddleocr import PaddleOCR
    try:
        engine = PaddleOCR(lang="en", enable_mkldnn=False)
    except Exception:
        engine = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

    def read(image, min_conf=0.0):
        import numpy as _np
        arr = _np.array(image.convert("RGB"))
        try:
            result = engine.ocr(arr)
        except TypeError:
            result = engine.ocr(arr, cls=False)

        out = []
        for page in (result or []):
            if isinstance(page, dict):
                texts = page.get("rec_texts") or []
                scores = page.get("rec_scores") or []
                boxes = page.get("rec_boxes") or page.get("rec_polys") or []
                for i, text in enumerate(texts):
                    conf = float(scores[i]) * 100.0 if i < len(scores) else 0.0
                    if conf < min_conf:
                        continue
                    box = boxes[i] if i < len(boxes) else None
                    if box is None:
                        continue
                    flat = _np.array(box).reshape(-1)
                    if flat.size == 4:
                        l, t, r, b = (float(v) for v in flat)
                    else:
                        pts = _np.array(box).reshape(-1, 2)
                        l, t = pts[:, 0].min(), pts[:, 1].min()
                        r, b = pts[:, 0].max(), pts[:, 1].max()
                    out.append((str(text), int(l), int(t), int(r), int(b), conf))
                continue
            for entry in (page or []):
                try:
                    box, (text, score) = entry
                except Exception:
                    continue
                conf = float(score) * 100.0
                if conf < min_conf:
                    continue
                xs = [pt[0] for pt in box]
                ys = [pt[1] for pt in box]
                out.append((str(text), int(min(xs)), int(min(ys)),
                            int(max(xs)), int(max(ys)), conf))
        return out
    return read


def select_ocr_engine(name: str, verbose: bool = True) -> bool:
    global OCR_BACKEND
    if not name or name == "tesseract":
        OCR_BACKEND = None
        return True
    loaders = {"rapid": _load_rapidocr, "paddle": _load_paddleocr}
    loader = loaders.get(name)
    if loader is None:
        if verbose:
            print(f"  unknown OCR engine {name!r}; staying on Tesseract.")
        return False
    try:
        OCR_BACKEND = _OCR_ENGINES.setdefault(name, loader())
        if verbose:
            print(f"  OCR engine: {name} (in process, no subprocess per read)")
        return True
    except Exception as exc:
        OCR_BACKEND = None
        if verbose:
            print(f"  {name} is not available ({type(exc).__name__}); "
                  f"staying on Tesseract.")
        return False


def _cache_key(source, region, min_conf, scale, kind):
    if not OCR_CACHE_ENABLED:
        return None
    if isinstance(source, Image.Image):
        serial = getattr(source, "_cabal_frame", None)
        if serial is None:
            return None
    else:
        serial = f"path:{source}"
    return (serial, tuple(region) if region else None, min_conf, scale, kind)


def forget_ocr_cache() -> None:
    _OCR_CACHE.clear()


def ocr_cache_stats() -> dict:
    return dict(_OCR_CACHE_STATS)


_OCR_CACHE_STATS = {"hits": 0, "misses": 0}


def _note_ocr(who: str, seconds: float) -> None:
    entry = _OCR_STATS.setdefault(who, {"calls": 0, "seconds": 0.0})
    entry["calls"] += 1
    entry["seconds"] += seconds


def ocr_profile_report() -> str:
    if not _OCR_STATS:
        return ""
    rows = sorted(_OCR_STATS.items(), key=lambda kv: -kv[1]["seconds"])
    calls = sum(v["calls"] for _, v in rows)
    total = sum(v["seconds"] for _, v in rows)
    out = ["", "OCR PROFILE -- every Tesseract launch this run",
           f"  {'caller':28}{'calls':>8}{'seconds':>10}{'avg ms':>9}{'share':>8}"]
    for who, v in rows:
        share = (100.0 * v["seconds"] / total) if total else 0.0
        avg = (1000.0 * v["seconds"] / v["calls"]) if v["calls"] else 0.0
        out.append(f"  {who:28}{v['calls']:>8}{v['seconds']:>10.1f}"
                   f"{avg:>9.0f}{share:>7.1f}%")
    out.append(f"  {'TOTAL':28}{calls:>8}{total:>10.1f}"
               f"{(1000.0 * total / calls) if calls else 0:>9.0f}{100.0:>7.1f}%")
    return chr(10).join(out)


DEBUG_ACTIONS = False

RECORD_KEEP = 1000
RECORD_PRUNE_SLACK = 100


try:
    import mss
except ImportError:
    sys.exit("Missing dependency 'mss'. Install it with:  pip install mss")

try:
    from PIL import Image, ImageChops, ImageOps, ImageStat
except ImportError:
    sys.exit("Missing dependency 'Pillow'. Install it with:  pip install Pillow")

SCRIPT_DIR = Path(__file__).resolve().parent


TABLE_READ_BUDGET = 45.0

DEFAULT_OUTDIR = SCRIPT_DIR / "screenshots"

open_capture = getattr(mss, "MSS", None) or mss.mss

ALZ_REGION = (2330, 872, 2525, 928)

TESSERACT_CANDIDATES = (
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
)


def find_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    for candidate in TESSERACT_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


ALZ_MAX_TEXT_HEIGHT = 0.5


def _isolate_digits(
    image: Image.Image, region: tuple[int, int, int, int]
) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    crop = image.crop(region).convert("RGB")
    scale = max(2, int(round(5 / max(0.2, LAYOUT.scale))))
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)

    px = crop.load()
    mask = Image.new("L", crop.size, 255)
    m = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            r, g, b = px[x, y]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > 110 and hi - lo > 45:
                m[x, y] = 0

    bbox = ImageOps.invert(mask).getbbox()
    if not bbox:
        return None

    if (bbox[3] - bbox[1]) > (crop.height * ALZ_MAX_TEXT_HEIGHT):
        return None

    prepared = ImageOps.expand(mask.crop(bbox), border=60, fill=255).convert("RGB")
    source_box = (
        region[0] + bbox[0] // scale,
        region[1] + bbox[1] // scale,
        region[0] + bbox[2] // scale,
        region[1] + bbox[3] // scale,
    )
    return prepared, source_box


_ALZ_TMP_SEQ = itertools.count(1)

def get_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    debug_path: Path | None = None,
) -> int:
    tesseract = find_tesseract()
    if tesseract is None:
        print(
            "Alz: skipped, Tesseract not found "
            "(winget install UB-Mannheim.TesseractOCR)",
            file=sys.stderr,
        )
        return 0

    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = region if region is not None else ALZ_REGION

    if region[2] > image.width or region[3] > image.height:
        print(
            f"Alz: skipped, region {region} falls outside the "
            f"{image.width}x{image.height} image",
            file=sys.stderr,
        )
        return 0

    found = _isolate_digits(image, region)
    if found is None:
        return 0
    prepared, _ = found

    tmp = debug_path or (SCRIPT_DIR /
                         f".alz_tmp_{os.getpid()}_{next(_ALZ_TMP_SEQ)}.png")
    try:
        prepared.save(tmp)
        _ocr_t0 = time.monotonic()
        result = subprocess.run(
            [
                tesseract, str(tmp), "stdout",
                "--psm", "7",
                "-c", "tessedit_char_whitelist=0123456789,",
            ],
            capture_output=True,
            text=True,
            timeout=TESSERACT_TIMEOUT,
        )
        _note_ocr('get_alz', time.monotonic() - _ocr_t0)
    except subprocess.TimeoutExpired:
        print(f"Alz: skipped, Tesseract did not respond within "
              f"{TESSERACT_TIMEOUT:g}s", file=sys.stderr)
        return 0
    except OSError as exc:
        print(f"Alz: skipped, could not run Tesseract ({exc})", file=sys.stderr)
        return 0
    finally:
        if debug_path is None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    if result.returncode != 0:
        print(f"Alz: skipped, Tesseract failed ({result.stderr.strip()})", file=sys.stderr)
        return 0

    digits = re.sub(r"[^0-9]", "", result.stdout)
    return int(digits) if digits else 0


def find_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[int, int, int, int] | None:
    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = region if region is not None else ALZ_REGION
    if region[2] > image.width or region[3] > image.height:
        return None

    found = _isolate_digits(image, region)
    if found is None:
        return None

    _, box = found
    width, height = region[2] - region[0], region[3] - region[1]
    if (box[2] - box[0]) >= width * 0.95 and (box[3] - box[1]) >= height * 0.95:
        return None

    ox, oy = origin
    return (box[0] + ox, box[1] + oy, box[2] + ox, box[3] + oy)


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def move_mouse(x: int, y: int) -> bool:
    if _suppressed(f"move the cursor to ({x}, {y})"):
        return True
    make_dpi_aware()
    ctypes.windll.user32.SetCursorPos.restype = ctypes.c_int
    moved = bool(ctypes.windll.user32.SetCursorPos(int(x), int(y)))
    if moved:
        cooldown()
    return moved


def cursor_position() -> tuple[int, int]:
    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    point = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


CURSOR_BLOCKED_HINT = (
    "Windows refused the cursor move. Cabal runs elevated, so a normal process "
    "cannot move the cursor while the game is in the foreground -- run this "
    "script as Administrator."
)


def move_mouse_to_alz(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int] | None = None,
    origin: tuple[int, int] = (0, 0),
) -> tuple[int, int] | None:
    box = find_alz(source, region, origin)
    if box is None:
        return None
    centre = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    if not move_mouse(*centre):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    return centre


def human(value: int) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


def make_dpi_aware() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def list_monitors(sct) -> None:
    for i, mon in enumerate(sct.monitors[1:], start=1):
        print(f"{i}: {mon['width']}x{mon['height']} at ({mon['left']},{mon['top']})")


def resolve_monitor(sct, choice: str) -> tuple[dict, str]:
    displays = sct.monitors[1:]

    if choice == "all":
        return sct.monitors[0], "all"
    if choice == "primary":
        for mon in displays:
            if mon["left"] == 0 and mon["top"] == 0:
                return mon, "primary"
        return displays[0], "primary"
    if choice.isdigit():
        index = int(choice)
        if not 1 <= index <= len(displays):
            sys.exit(
                f"Monitor {index} does not exist. Detected {len(displays)} "
                f"monitor(s). Run with --monitors to see them."
            )
        return displays[index - 1], f"monitor{index}"

    sys.exit(
        f"Invalid --monitor value {choice!r}. "
        "Use 'primary', 'all', or a 1-based monitor number."
    )


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, CAPTURE_SUFFIX_LIMIT):
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def prune_screenshots(outdir: Path, keep: int, protect: Path) -> int:
    shots = sorted(
        outdir.glob("screenshot_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in shots[max(keep, 1):]:
        if old.resolve() == protect.resolve():
            continue
        try:
            old.unlink()
            removed += 1
        except OSError as exc:
            print(f"Could not delete {old.name}: {exc}", file=sys.stderr)
    return removed


def copy_to_clipboard(png_bytes: bytes) -> None:
    import io

    image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    with io.BytesIO() as buf:
        image.save(buf, "BMP")
        dib = buf.getvalue()[14:]

    CF_DIB, GMEM_MOVEABLE = 8, 0x0002
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
    if not handle:
        print("Skipping --clipboard: GlobalAlloc failed", file=sys.stderr)
        return

    pointer = kernel32.GlobalLock(handle)
    ctypes.memmove(pointer, dib, len(dib))
    kernel32.GlobalUnlock(handle)

    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(ctypes.c_void_p(handle))
        print("Skipping --clipboard: could not open the clipboard", file=sys.stderr)
        return
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_DIB, handle):
            kernel32.GlobalFree(ctypes.c_void_p(handle))
            print("Skipping --clipboard: SetClipboardData failed", file=sys.stderr)
            return
    finally:
        user32.CloseClipboard()

    print("Copied to clipboard.")


def take_screenshot(monitor: str = "primary", delay: float = 0.0):
    make_dpi_aware()
    with open_capture() as sct:
        region, label = resolve_monitor(sct, monitor)
        if delay > 0:
            time.sleep(delay)
        shot = sct.grab(region)
    origin = (region["left"], region["top"])
    return mss.tools.to_png(shot.rgb, shot.size), shot.width, shot.height, label, origin


REF_SCREEN = (2560, 1440)
REF_TRADE_ORIGIN = (10, 30)
REF_TRADE_SIZE = (1225, 1035)

REF_ANCHORS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Trade", (608, 19)),
    ("Name", (492, 119)),
    ("Adjust", (919, 65)),
    ("Function", (1126, 118)),
    ("Selling", (331, 982)),
    ("Refresh", (1119, 981)),
)
REF_ANCHORS_EXTRA: tuple[tuple[str, tuple[int, int]], ...] = (
    ("Purchase", (128, 67)),
    ("Item", (142, 122)),
    ("Status", (1010, 119)),
    ("Period", (55, 869)),
    ("Expired", (503, 982)),
    ("Sold", (674, 980)),
    ("Total", (863, 980)),
)

REF_ANCHORS_ALL: tuple[tuple[str, tuple[int, int]], ...] = (
    REF_ANCHORS + REF_ANCHORS_EXTRA)

MIN_ANCHOR_BASELINE = 300.0
MIN_ANCHOR_SPREAD = 250.0
NEAR_ANCHOR_MIN_CONF = 70.0
MIN_ANCHORS_AFTER_DROP = 4
SCALE_LIMITS = (0.4, 2.5)
CALIBRATION_FILE = SCRIPT_DIR / "calibration.json"


@dataclass(frozen=True)
class Layout:
    screen: tuple[int, int]
    origin: tuple[int, int]
    scale: float
    client: tuple[int, int, int, int] | None = None
    measured_from: str = "reference"

    def x(self, value: float) -> int:
        return int(round(self.origin[0] + value * self.scale))

    def y(self, value: float) -> int:
        return int(round(self.origin[1] + value * self.scale))

    def point(self, ref: tuple[float, float]) -> tuple[int, int]:
        return (self.x(ref[0]), self.y(ref[1]))

    def box(self, ref: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        left, top, right, bottom = ref
        return (self.x(left), self.y(top), self.x(right), self.y(bottom))

    def length(self, value: float) -> int:
        return int(round(value * self.scale))

    @property
    def trade(self) -> tuple[int, int, int, int]:
        return self.box((0, 0, REF_TRADE_SIZE[0], REF_TRADE_SIZE[1]))

    def describe(self) -> str:
        return (f"screen {self.screen[0]}x{self.screen[1]}, Trade window at "
                f"{self.origin}, scale {self.scale:.3f} ({self.measured_from})")


REF_FUNCTION_COLUMN_X = 1116
REF_DIALOG_BUTTON_MIN_X = 1200

LAYOUT = Layout(screen=REF_SCREEN, origin=REF_TRADE_ORIGIN, scale=1.0,
                measured_from="reference defaults")

TRADE_REGION = LAYOUT.trade
POPUP_REGION = (500, 350, 2100, 1150)


def _clamp_box(box: tuple[int, int, int, int],
               screen: tuple[int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = box
    left, top = max(0, left), max(0, top)
    right, bottom = min(screen[0], right), min(screen[1], bottom)
    return (left, top, max(left + 1, right), max(top + 1, bottom))


CONFIRM_WORD = "Confirmation"
DISMISS_WORD = "Cancel"
RECEIPT_WORD = "Receive"
DIALOG_BUTTON_MIN_X = 1200
DIALOG_BUTTON_MIN_Y = 800
MAX_CONFIRM_STEPS = 3
EXTENSION_RECHECK_SECONDS = 4.0
DIALOG_TEXT_MIN_CONF = 25.0


@dataclass
class Word:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    conf: float

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)


def _prep_for_text(image: Image.Image, region: tuple[int, int, int, int], scale: int):
    crop = image.crop(region).convert("L")
    crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
    return ImageOps.autocontrast(ImageOps.invert(crop))


_CALIBRATED = False


def _ocr_reference_scale() -> float:
    if _CALIBRATED:
        return LAYOUT.scale
    try:
        make_dpi_aware()
        client = client_rect()
        if client:
            width = client[2] - client[0]
            height = client[3] - client[1]
            ref_w = REF_CLIENT[2] - REF_CLIENT[0]
            ref_h = REF_CLIENT[3] - REF_CLIENT[1]
            if width > 100 and height > 100:
                guess = min(width / ref_w, height / ref_h)
                if SCALE_LIMITS[0] <= guess <= SCALE_LIMITS[1]:
                    return guess
    except Exception:
        pass
    return LAYOUT.scale


def find_words(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
    scale: int | None = None,
) -> list[Word]:
    if scale is None:
        scale = max(2, min(6, int(round(2 / max(_ocr_reference_scale(), 0.34)))))
    _key = _cache_key(source, region, 0.0, scale, "words")
    if _key is not None and _key in _OCR_CACHE:
        _OCR_CACHE_STATS["hits"] += 1
        return [w for w in _OCR_CACHE[_key] if w.conf >= min_conf]
    if _key is not None:
        _OCR_CACHE_STATS["misses"] += 1

    tesseract = find_tesseract()
    if tesseract is None:
        print("Tesseract not found (winget install UB-Mannheim.TesseractOCR); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    image = source if isinstance(source, Image.Image) else Image.open(source)
    region = (
        max(0, region[0]), max(0, region[1]),
        min(image.width, region[2]), min(image.height, region[3]),
    )
    prepared = _prep_for_text(image, region, scale)

    if OCR_BACKEND is not None:
        _t0 = time.monotonic()
        try:
            raw = OCR_BACKEND(prepared, 0.0)
        except Exception:
            raw = []
        _note_ocr(f"backend:{getattr(OCR_BACKEND, '__name__', 'custom')}",
                  time.monotonic() - _t0)
        words = []
        for text, wl, wt, wr, wb, conf in raw:
            if not str(text).strip():
                continue
            words.append(Word(
                text=str(text).strip(),
                left=region[0] + int(wl / max(1, scale)),
                top=region[1] + int(wt / max(1, scale)),
                right=region[0] + int(wr / max(1, scale)),
                bottom=region[1] + int(wb / max(1, scale)),
                conf=float(conf)))
        if _key is not None:
            _OCR_CACHE[_key] = list(words)
        return [w for w in words if w.conf >= min_conf]

    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    try:
        _ocr_t0 = time.monotonic()
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "11", "tsv"],
            input=buf.getvalue(),
            capture_output=True,
            timeout=TESSERACT_TIMEOUT,
        )
        _note_ocr('find_words', time.monotonic() - _ocr_t0)
    except subprocess.TimeoutExpired:
        print(f"Tesseract did not respond within {TESSERACT_TIMEOUT:g}s; "
              "treating this frame as unreadable.", file=sys.stderr)
        return []
    except OSError as exc:
        print(f"Could not run Tesseract ({exc}); treating this frame as "
              "unreadable.", file=sys.stderr)
        return []
    if result.returncode != 0:
        print(f"Tesseract failed ({result.stderr.decode(errors='replace').strip()}); "
              "treating this frame as unreadable.", file=sys.stderr)
        return []

    words: list[Word] = []
    reader = csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8", errors="replace")),
        delimiter="\t", quoting=csv.QUOTE_NONE,
    )
    for row in reader:
        text = (row.get("text") or "").strip()
        if not text:
            continue
        try:
            conf = float(row["conf"])
            left, top = int(row["left"]), int(row["top"])
            width, height = int(row["width"]), int(row["height"])
        except (TypeError, ValueError, KeyError):
            continue
        if conf < 0.0:
            continue
        words.append(Word(
            text=text,
            left=region[0] + left // scale,
            top=region[1] + top // scale,
            right=region[0] + (left + width) // scale,
            bottom=region[1] + (top + height) // scale,
            conf=conf,
        ))
    if _key is not None:
        _OCR_CACHE[_key] = list(words)
    return [w for w in words if w.conf >= min_conf]


_DIGIT_LOOKALIKES = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "I": "1", "i": "1", "l": "1", "|": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5",
    "G": "6",
    "T": "7",
    "B": "8",
})


INK_CONTRAST_MIN = 160


def _ink_box(prepared: Image.Image, threshold: int = 128):
    return prepared.point(lambda v: 255 if v < threshold else 0).getbbox()


def read_number(
    source: "Image.Image | Path | str",
    region: "tuple[int, int, int, int]",
    min_conf: float = 0.0,
) -> "int | None":
    _key = _cache_key(source, region, min_conf, None, "number")
    if _key is not None and _key in _OCR_CACHE:
        _OCR_CACHE_STATS["hits"] += 1
        return _OCR_CACHE[_key]
    if _key is not None:
        _OCR_CACHE_STATS["misses"] += 1
    value = _read_number_uncached(source, region, min_conf)
    if _key is not None:
        _OCR_CACHE[_key] = value
    return value


def _read_number_uncached(
    source: Image.Image | Path | str,
    region: tuple[int, int, int, int],
    min_conf: float = 0.0,
) -> int | None:

    image = source if isinstance(source, Image.Image) else Image.open(source)
    box = (max(0, region[0]), max(0, region[1]),
           min(image.width, region[2]), min(image.height, region[3]))
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None
    lo, hi = image.crop(box).convert("L").getextrema()
    if hi - lo < INK_CONTRAST_MIN:
        return None

    words = sorted(find_words(source, region, min_conf), key=lambda w: w.left)
    value = _digits("".join(w.text for w in words))
    if value is not None:
        return value

    tesseract = find_tesseract()
    if tesseract is None:
        return None

    prepared = ImageOps.expand(_prep_for_text(image, box, 4), border=24, fill=255)
    buf = io.BytesIO()
    prepared.save(buf, "PNG")
    try:
        _ocr_t0 = time.monotonic()
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "7",
             "-c", "tessedit_char_whitelist=0123456789,"],
            input=buf.getvalue(), capture_output=True, timeout=TESSERACT_TIMEOUT,
        )
        _note_ocr('read_number', time.monotonic() - _ocr_t0)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode == 0:
        value = _digits(result.stdout.decode("utf-8", errors="replace"))
        if value is not None:
            return value

    try:
        _ocr_t0 = time.monotonic()
        result = subprocess.run(
            [tesseract, "stdin", "stdout", "--psm", "10"],
            input=buf.getvalue(), capture_output=True, timeout=TESSERACT_TIMEOUT,
        )
        _note_ocr('read_number', time.monotonic() - _ocr_t0)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    glyph = re.sub(r"[^0-9A-Za-z|]", "",
                   result.stdout.decode("utf-8", errors="replace"))
    if len(glyph) != 1 or glyph.isdigit():
        return None

    ink = _ink_box(prepared)
    if ink is None:
        return None
    ink_w, ink_h = ink[2] - ink[0], ink[3] - ink[1]
    if ink_h <= 0 or ink_w > ink_h * 1.15:
        return None
    return _digits(glyph.translate(_DIGIT_LOOKALIKES))


def find_text(
    source: Image.Image | Path | str,
    needle: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> list[Word]:
    needle = needle.casefold()
    hits = [w for w in find_words(source, region, min_conf) if needle in w.text.casefold()]
    return sorted(hits, key=lambda w: w.top)


def _text_lines(words: list[Word],
                tolerance: "int | None" = None) -> list[list[Word]]:
    if tolerance is None:
        tolerance = max(4, LAYOUT.length(10))
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.top, w.left)):
        for line in lines:
            if abs(word.centre[1] - line[0].centre[1]) <= tolerance:
                line.append(word)
                break
        else:
            lines.append([word])
    for line in lines:
        line.sort(key=lambda w: w.left)
    return lines


def _minimal_window(line: list[Word], fragments: tuple[str, ...]) -> list[Word] | None:
    window = None
    for start in range(len(line)):
        joined = ""
        for end in range(start, len(line)):
            joined += line[end].text
            normalised = _normalise(joined)
            if all(fragment in normalised for fragment in fragments):
                span = line[start:end + 1]
                if window is None or len(span) < len(window):
                    window = span
                break
    return window


def _span_centre(words: list[Word]) -> tuple[int, int]:
    left = min(w.left for w in words)
    right = max(w.right for w in words)
    top = min(w.top for w in words)
    bottom = max(w.bottom for w in words)
    return ((left + right) // 2, (top + bottom) // 2)


def find_phrase(
    source: Image.Image | Path | str,
    phrase: str,
    region: tuple[int, int, int, int],
    min_conf: float = 40.0,
) -> tuple[int, int] | None:
    target = _normalise(phrase)
    if not target:
        return None
    for line in _text_lines(find_words(source, region, min_conf)):
        if target not in _normalise("".join(w.text for w in line)):
            continue
        window = _minimal_window(line, (target,))
        if window is None:
            continue
        return _span_centre(window)
    return None


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long), ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _InputUnion)]


INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12


NO_INPUT = False


def _suppressed(what: str) -> bool:
    if not NO_INPUT:
        return False
    print(f"[dry run] would {what} - suppressed, the game is not touched")
    return True


def cooldown(seconds: float | None = None) -> None:
    time.sleep(ACTION_COOLDOWN if seconds is None else seconds)


SEND_RETRY_PAUSE = 0.12

def _send(event: _Input, attempts: int = SEND_ATTEMPTS) -> None:
    for attempt in range(1, attempts + 1):
        try:
            if ctypes.windll.user32.SendInput(
                    1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                if attempt > 1:
                    print(f"  (input accepted on attempt {attempt})",
                          file=sys.stderr)
                return
        except OSError:
            pass
        if attempt < attempts:
            time.sleep(SEND_RETRY_PAUSE)
    raise PermissionError(CURSOR_BLOCKED_HINT)


def _release(event: _Input, what: str,
             attempts: int = RELEASE_ATTEMPTS) -> bool:
    for _ in range(attempts):
        try:
            if ctypes.windll.user32.SendInput(
                    1, ctypes.byref(event), ctypes.sizeof(_Input)) == 1:
                return True
        except OSError:
            pass
        time.sleep(0.05)
    print(f"WARNING: could not release {what}; it may still be held down. "
          "Press it once by hand before continuing.", file=sys.stderr)
    return False


def _release_left_button() -> bool:
    return _release(_mouse_event(MOUSEEVENTF_LEFTUP), "the left mouse button")


def _release_right_button() -> bool:
    return _release(_mouse_event(MOUSEEVENTF_RIGHTUP), "the right mouse button")


def _release_key(vk: int) -> bool:
    return _release(_key_event(vk, up=True), f"key 0x{vk:02X}")


def release_modifiers() -> None:
    for vk in (VK_CONTROL, VK_MENU, VK_SHIFT):
        try:
            _release(_key_event(vk, up=True), f"modifier 0x{vk:02X}", attempts=1)
        except Exception:
            pass


def _mouse_event(flags: int) -> _Input:
    return _Input(type=INPUT_MOUSE,
                  u=_InputUnion(mi=_MouseInput(0, 0, 0, flags, 0, None)))


def _key_event(vk: int, up: bool) -> _Input:
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    return _Input(type=INPUT_KEYBOARD,
                  u=_InputUnion(ki=_KeyInput(vk, scan, flags, 0, None)))


VK_BACK = 0x08
VK_ESCAPE = 0x1B
CLEAR_KEYSTROKES = 13


VK_N = 0x4E
VK_I = 0x49


def press_key(vk: int, settle: float = 0.5, what: str = "") -> None:
    if _suppressed(f"press {what or hex(vk)}"):
        return
    _send(_key_event(vk, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(vk)
    time.sleep(settle)
    cooldown()


def open_inventory(timeout: float = 6.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    say("  opening the Inventory panel.")
    focus_game()
    _send(_key_event(VK_I, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(VK_I)
    cooldown()
    deadline = time.monotonic() + timeout
    pressed_again = False
    while time.monotonic() < deadline:
        time.sleep(0.4)
        if inventory_origin() is not None:
            say("  the Inventory panel is open.")
            return True
        if not pressed_again:
            pressed_again = True
            say("  it was already open and that closed it; toggling back.")
            _send(_key_event(VK_I, up=False))
            try:
                time.sleep(0.05)
            finally:
                _release_key(VK_I)
            cooldown()
    say("  the Inventory panel did not open.")
    record("inventory.would_not_open")
    return False


def press_escape(settle: float = 0.5) -> None:
    if _suppressed("press Escape"):
        return
    _send(_key_event(VK_ESCAPE, up=False))
    try:
        time.sleep(0.05)
    finally:
        _release_key(VK_ESCAPE)
    time.sleep(settle)
    cooldown()
    debug_shot("escape")


def type_number(value: int, per_key: float = TYPE_COOLDOWN,
                clear_first: bool = True, clear: int | None = None) -> None:
    if _suppressed(f"type {value}"):
        return

    def tap(vk: int) -> None:
        _send(_key_event(vk, up=False))
        try:
            time.sleep(0.02)
        finally:
            _release_key(vk)
        time.sleep(per_key)

    if clear_first:
        for _ in range(CLEAR_KEYSTROKES if clear is None else clear):
            tap(VK_BACK)
    for ch in str(value):
        tap(0x30 + int(ch))
    cooldown()
    debug_shot("type", value=value)


WHEEL_SETTLE = 0.12


def scroll_wheel(x: int, y: int, notches: int, settle: float = WHEEL_SETTLE,
                 checked: bool = False) -> None:
    if _suppressed(f"scroll {notches:+d} notch(es) at ({x}, {y})"):
        return

    if not checked and not table_scrollable(verbose=True):
        raise Aborted(
            f"refusing to scroll {notches:+d} notch(es) at ({x}, {y}): the "
            "listings table is not what the wheel would reach")

    park_cursor()
    _guard_shot = grab()
    blocker = (dialog_button_band(DISMISS_WORD, source=_guard_shot)
               or dialog_button_band(CONFIRM_WORD, source=_guard_shot))
    if blocker is not None:
        raise Aborted(
            f"refusing to scroll {notches:+d} notch(es) at ({x}, {y}): a "
            f"dialog is open over the table (its {DISMISS_WORD}/"
            f"{CONFIRM_WORD} button reads at {blocker.centre}), so the wheel "
            f"would move nothing and the row under the cursor afterwards "
            f"would not be the row that was asked for")

    make_dpi_aware()
    if cursor_position() != (int(x), int(y)):
        if not move_mouse(x, y):
            raise PermissionError(CURSOR_BLOCKED_HINT)
    step = (WHEEL_DELTA if notches > 0 else -WHEEL_DELTA) & 0xFFFFFFFF
    for _ in range(abs(notches)):
        _send(_Input(type=INPUT_MOUSE,
                     u=_InputUnion(mi=_MouseInput(0, 0, step,
                                                  MOUSEEVENTF_WHEEL, 0, None))))
        time.sleep(settle)
    cooldown()


CLICK_APPROACH_DY = 24
CLICK_APPROACH_SETTLE = 0.12
CLICK_HOVER_SETTLE = 0.30


def click(x: int, y: int, settle: float = 0.15) -> None:
    if _suppressed(f"click ({x}, {y})"):
        return
    make_dpi_aware()
    if not move_mouse(x, y - CLICK_APPROACH_DY):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(CLICK_APPROACH_SETTLE)
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(max(settle, CLICK_HOVER_SETTLE))

    _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
    try:
        time.sleep(0.09)
    finally:
        _release_left_button()
    time.sleep(0.05)
    cooldown()
    debug_shot("click", x=x, y=y)


def ctrl_click(x: int, y: int, settle: float = 0.15) -> None:
    if _suppressed(f"Ctrl+Click ({x}, {y})"):
        return
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    _send(_key_event(VK_CONTROL, up=False))
    try:
        time.sleep(0.25)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.12)
        finally:
            _release_left_button()
        time.sleep(0.25)
    finally:
        _release_key(VK_CONTROL)
        time.sleep(0.08)
    cooldown()
    debug_shot("ctrl_click", x=x, y=y)


def right_click(x: int, y: int, settle: float = 0.15) -> None:
    if _suppressed(f"right-click ({x}, {y})"):
        return
    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    _send(_mouse_event(MOUSEEVENTF_RIGHTDOWN))
    try:
        time.sleep(0.09)
    finally:
        _release_right_button()
    time.sleep(0.05)
    cooldown()
    debug_shot("right_click", x=x, y=y)


def alt_click(x: int, y: int, settle: float = 0.15) -> None:
    if not (vendor_shop_open() or _point_in_inventory_grid(x, y)):
        raise Aborted(
            f"refusing Alt+Click at ({x}, {y}): neither the vendor Shop nor "
            "the inventory grid is under that point, so it would land in the "
            "game world and walk the character")

    if _suppressed(f"Alt+Click ({x}, {y})"):
        return

    make_dpi_aware()
    if not move_mouse(x, y):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    time.sleep(settle)

    _send(_key_event(VK_MENU, up=False))
    try:
        time.sleep(0.25)
        _send(_mouse_event(MOUSEEVENTF_LEFTDOWN))
        try:
            time.sleep(0.12)
        finally:
            _release_left_button()
        time.sleep(0.25)
    finally:
        _release_key(VK_MENU)
        time.sleep(0.08)
    cooldown()
    debug_shot("alt_click", x=x, y=y)


def grab() -> Image.Image:
    global _last_shot, _FRAME_SERIAL
    png, _, _, _, _ = take_screenshot()
    _last_shot = Image.open(io.BytesIO(png))
    _OCR_CACHE.clear()
    _FRAME_SERIAL += 1
    try:
        _last_shot._cabal_frame = _FRAME_SERIAL
    except Exception:
        pass
    return _last_shot


RECORD_DIR = SCRIPT_DIR / "unit_tests" / "corpus"
_last_shot: "Image.Image | None" = None
_record_seq = 0


def debug_shot(action: str, /, **context) -> None:
    if not (DEBUG_ACTIONS and RECORD_ENABLED):
        return
    try:
        record("do.action", grab(), action=action, **context)
    except Exception:
        pass


def record(label: str, shot: "Image.Image | None" = None, /, **context) -> None:
    global _record_seq
    if not RECORD_ENABLED:
        return
    image = shot if shot is not None else _last_shot
    if image is None:
        return
    try:
        RECORD_DIR.mkdir(parents=True, exist_ok=True)
        if _record_seq == 0:
            highest = 0
            for existing in RECORD_DIR.glob("run_*.png"):
                digits = existing.stem[4:]
                if digits.isdigit():
                    highest = max(highest, int(digits))
            _record_seq = highest
        _record_seq += 1
        name = f"run_{_record_seq:05d}.png"
        image.save(RECORD_DIR / name)
        entry = {"file": name, "label": label,
                 "at": datetime.now().isoformat(timespec="seconds"),
                 "layout": {"origin": list(LAYOUT.origin),
                            "scale": round(LAYOUT.scale, 6),
                            "screen": list(LAYOUT.screen)}}
        for key, value in context.items():
            if value is None:
                continue
            entry["ctx_" + key if key in ("file", "label", "at") else key] = value
        with (RECORD_DIR / "run_index.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        prune_recordings()
    except Exception:
        pass


def prune_recordings(keep: int = RECORD_KEEP,
                     slack: int = RECORD_PRUNE_SLACK) -> int:
    try:
        frames = []
        for path in RECORD_DIR.glob("run_*.png"):
            digits = path.stem[4:]
            if digits.isdigit():
                frames.append((int(digits), path))
        if len(frames) <= keep + slack:
            return 0

        frames.sort()
        doomed = frames[:len(frames) - keep]
        gone = set()
        for _seq, path in doomed:
            try:
                path.unlink()
                gone.add(path.name)
            except OSError:
                pass
        if not gone:
            return 0

        index = RECORD_DIR / "run_index.jsonl"
        if index.exists():
            tmp = index.with_suffix(".jsonl.tmp")
            kept = 0
            with index.open("r", encoding="utf-8", errors="replace") as src, \
                    tmp.open("w", encoding="utf-8") as dst:
                for line in src:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        name = json.loads(stripped).get("file")
                    except Exception:
                        dst.write(line)
                        kept += 1
                        continue
                    if name not in gone:
                        dst.write(line)
                        kept += 1
            os.replace(tmp, index)
        return len(gone)
    except Exception:
        return 0


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
UOI_NAME = 2
DESKTOP_SWITCHDESKTOP = 0x0100


def session_locked() -> bool:
    user32 = ctypes.windll.user32
    handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if not handle:
        return True
    try:
        name = ctypes.create_unicode_buffer(256)
        needed = ctypes.c_ulong()
        if user32.GetUserObjectInformationW(
            handle, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)
        ):
            return name.value.casefold() != "default"
        return False
    finally:
        user32.CloseDesktop(handle)


def keep_awake(enable: bool = True) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetThreadExecutionState.restype = ctypes.c_ulong
    flags = ES_CONTINUOUS
    if enable:
        flags |= ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    return bool(kernel32.SetThreadExecutionState(flags))


def find_game_window() -> int | None:
    user32 = ctypes.windll.user32
    found: list[int] = []

    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if GAME_TITLE_HINT.casefold() in buf.value.casefold():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proto(callback), None)
    return found[0] if found else None


def focus_game(settle: float = 0.35) -> bool:
    user32 = ctypes.windll.user32
    hwnd = find_game_window()
    if hwnd is None:
        return False

    if user32.GetForegroundWindow() == hwnd:
        return True

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)

    if user32.GetForegroundWindow() == hwnd:
        return True

    try:
        _send(_key_event(VK_MENU, up=False))
        try:
            time.sleep(0.03)
        finally:
            _release_key(VK_MENU)
        time.sleep(0.05)
    except PermissionError:
        _release_key(VK_MENU)

    user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    if user32.GetForegroundWindow() == hwnd:
        return True

    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    foreground_thread = user32.GetWindowThreadProcessId(
        user32.GetForegroundWindow(), None)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    for thread in {target_thread, foreground_thread} - {current_thread, 0}:
        user32.AttachThreadInput(current_thread, thread, True)
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for thread in {target_thread, foreground_thread} - {current_thread, 0}:
            user32.AttachThreadInput(current_thread, thread, False)
    time.sleep(settle)
    return user32.GetForegroundWindow() == hwnd


NAME_COLUMN = (275, 715)
PREMIUM_SLOT_MARKER = "premium"
REF_ROW_PITCH = 79

TABLE_HEAD_BAND = (10, 140, 1236, 275)

SCROLL_POINT = (620, 800)
BUTTON_WORDS = ("Change", "Receive", "Register")
QTY_COL_MIN_CONF = 15.0
PARK_POINT = (600, 45)

TOOLTIP_CLEAR_SECONDS = 0.45

def forget_park_point() -> None:
    return None


def park_point() -> tuple[int, int]:
    return PARK_POINT


@dataclass
class Row:
    index: int
    name: str
    change: tuple[int, int]
    top: int
    bottom: int
    action: str
    price: int | None = None
    qty: int | None = None
    status: str = ""

    @property
    def cancellable(self) -> bool:
        return self.action == "change"

    @property
    def occupied(self) -> bool:
        return self.action in ("change", "receive")

    @property
    def empties_on_collect(self) -> bool:
        if self.action != "receive":
            return False
        if status_is_complete(self.status):
            return True
        if status_on_sale(self.status):
            return False
        return self.qty == 0


class FatalAbort(Exception):
    pass


def _model_key(name: str) -> str:
    return _floor_key(item_name(_PACK_ANYWHERE.sub(" ", name or "")))


class ShopDiverged(FatalAbort):
    pass


class ShopModel:

    def __init__(self) -> None:
        self._slots: dict[int, dict] = {}
        self._ready = False
        self.enforce = False
        self.divergences = 0

    @property
    def ready(self) -> bool:
        return self._ready

    def reset(self, reason: str = "") -> None:
        self._slots.clear()
        self._ready = False
        record("shopmodel.reset", reason=reason)

    def adopt(self, pairs: "list") -> None:
        covered = max((int(i) for i, _ in pairs), default=0)
        if covered < SHOP_ROW_CAPACITY:
            record("shopmodel.seed_refused", covered=covered,
                   capacity=SHOP_ROW_CAPACITY)
            raise ShopDiverged(
                f"a walk covering rows 1-{covered} cannot seed a "
                f"{SHOP_ROW_CAPACITY}-slot model: rows {covered + 1}-"
                f"{SHOP_ROW_CAPACITY} were never looked at, and treating them "
                "as empty is what sends a listing somewhere unpredicted.")

        self._slots.clear()
        for index, row in pairs:
            if not 1 <= int(index) <= SHOP_ROW_CAPACITY:
                continue
            if getattr(row, "occupied", False):
                name = getattr(row, "name", "") or ""
                try:
                    cost = purchase_cost_basis(name)
                except Exception:
                    cost = 0
                try:
                    floor, _why = effective_floor(item_price_floor(name), "",
                                                  cost if COST_FLOOR_ON_RELIST else 0)
                except Exception:
                    floor = 0
                self._slots[int(index)] = {
                    "name": name,
                    "qty": getattr(row, "qty", None),
                    "price": getattr(row, "price", None),
                    "floor": floor or 0,
                    "cost": cost or 0,
                }
        self._ready = True
        record("shopmodel.adopted", occupied=len(self._slots),
               slots=",".join(str(i) for i in sorted(self._slots)))

    def occupied_count(self) -> int:
        return len(self._slots)

    def is_empty(self, index: int) -> bool:
        return int(index) not in self._slots

    def content(self, index: int) -> "dict | None":
        entry = self._slots.get(int(index))
        return dict(entry) if entry else None

    def first_empty(self) -> "int | None":
        for i in range(1, SHOP_ROW_CAPACITY + 1):
            if i not in self._slots:
                return i
        return None

    def occupied_rows(self) -> list[int]:
        return sorted(self._slots)

    def describe(self) -> str:
        if not self._ready:
            return "  row model: not seeded"
        out = [f"  ROW MODEL -- {len(self._slots)} of {SHOP_ROW_CAPACITY} "
               f"slot(s) in use, next listing lands at row "
               f"{self.first_empty() or '- (full)'}",
               f"    {'ROW':<5}{'ITEM':<28}{'QTY':>7}{'PRICE':>14}"
               f"{'FLOOR':>14}{'COST':>12}"]
        for i in range(1, SHOP_ROW_CAPACITY + 1):
            e = self._slots.get(i)
            if not e:
                out.append(f"    {i:<5}{'(empty)':<28}")
                continue
            out.append(
                f"    {i:<5}{(e.get('name') or '?')[:27]:<28}"
                f"{(f'{e['qty']:,}' if e.get('qty') else '-'):>7}"
                f"{(f'{e['price']:,}' if e.get('price') else '-'):>14}"
                f"{(f'{e['floor']:,}' if e.get('floor') else '-'):>14}"
                f"{(f'{e['cost']:,}' if e.get('cost') else '-'):>12}")
        return "\n".join(out)

    def register(self, name: str, qty: "int | None" = None,
                 price: "int | None" = None, floor: "int | None" = None,
                 cost: "int | None" = None) -> int:
        index = self.first_empty()
        if index is None:
            raise ShopDiverged(
                f"the model has all {SHOP_ROW_CAPACITY} slots occupied, so "
                "there is nowhere for this listing to land")
        self._slots[index] = {"name": name or "", "qty": qty, "price": price,
                              "floor": floor or 0, "cost": cost or 0}
        record("shopmodel.register", row=index, item=name, qty=qty,
               price=price, floor=floor, cost=cost)
        return index

    def floor_of(self, index: int) -> int:
        return int((self._slots.get(int(index)) or {}).get("floor") or 0)

    def cost_of(self, index: int) -> int:
        return int((self._slots.get(int(index)) or {}).get("cost") or 0)

    def below_floor(self, index: int, price: int) -> bool:
        floor = self.floor_of(index)
        return bool(floor and price < floor)

    def cancel(self, index: int) -> None:
        self._slots.pop(int(index), None)
        record("shopmodel.cancel", row=int(index))

    def collect(self, index: int, empties: bool,
                qty_left: "int | None" = None) -> None:
        index = int(index)
        if empties:
            self._slots.pop(index, None)
            self.reset("a collection freed a slot; the table will renumber")
        elif index in self._slots and qty_left is not None:
            self._slots[index]["qty"] = qty_left
        record("shopmodel.collect", row=index, emptied=bool(empties),
               qty_left=qty_left)

    def check(self, index: int, row) -> None:
        if not self._ready:
            return
        index = int(index)
        mine = self._slots.get(index)
        theirs = row if getattr(row, "occupied", False) else None
        seen = getattr(row, "name", "?")

        why = None
        if mine is None and theirs is not None:
            why = (f"row {index} holds {seen!r}, but the model has that slot "
                   "EMPTY. A registration would have been sent there.")
        elif mine is not None and theirs is None:
            why = (f"row {index} is empty, but the model has it holding "
                   f"{mine['name']!r}. Something removed it that this script "
                   "did not do.")
        elif mine is not None and theirs is not None and (
                _model_key(mine["name"]) != _model_key(seen or "")):
            why = (f"row {index} holds {seen!r}, but the model has "
                   f"{mine['name']!r}. Acting on this row would touch the "
                   "wrong listing.")
        if why is None:
            return

        self.divergences += 1
        record("shopmodel.diverged", row=index, seen=seen,
               model=(mine or {}).get("name"), enforcing=self.enforce)
        enforcing = self.enforce
        self.reset("diverged")
        self.enforce = enforcing
        if enforcing:
            raise ShopDiverged(why + " Stopping rather than acting on it.")
        print(f"  [row model] {why} Not enforcing; the model stands down.")
        self.reset("diverged")


SHOP = ShopModel()


def park_cursor(settle: float = 0.0) -> None:
    if not move_mouse(*park_point()):
        raise PermissionError(CURSOR_BLOCKED_HINT)
    remaining = (settle or 0.0) - ACTION_COOLDOWN
    if remaining > 0:
        time.sleep(remaining)


NPC_NAME_FRAGMENT = "katerina"
NPC_TITLE_FRAGMENT = "agentshop"
NPC_NAME_WORD = "yekaterina"
NPC_TITLE_WORDS = ("agent", "shop")
NPC_WORD_SIMILARITY = 0.80


def _npc_label_words(line: "list[Word]") -> "tuple[bool, bool]":
    texts = [_normalise(w.text) for w in line]
    texts = [t for t in texts if t]
    name = _mentions(texts, NPC_NAME_WORD, NPC_WORD_SIMILARITY)
    if not name:
        name = any(NPC_NAME_FRAGMENT in t for t in texts)
    title = all(
        _mentions(texts, want, NPC_WORD_SIMILARITY)
        or any(want in t for t in texts)
        for want in NPC_TITLE_WORDS)
    return name, title
NPC_SEARCH_REGION = (600, 150, 1900, 900)
NPC_EXCLUDE_FRACTIONS = (
    (0.0000, 0.4945, 0.3516, 1.0000),
    (0.7227, 0.0000, 1.0000, 1.0000),
)
NPC_EXCLUDE_ZONES = (
    (0, 700, 900, 1392),
    (1850, 23, 2560, 1392),
)
NPC_CLICK_ATTEMPTS = 100
NPC_BODY_OFFSET = (0, 120)


def _npc_click_offsets(attempts: int = NPC_CLICK_ATTEMPTS) -> tuple:
    cx, cy = NPC_BODY_OFFSET
    step_y = max(4, LAYOUT.length(10))
    step_x = max(6, LAYOUT.length(15))
    span_y = LAYOUT.length(110), LAYOUT.length(160)
    span_x = LAYOUT.length(60)
    grid = [(cx + dx, cy + dy)
            for dy in range(-span_y[0], span_y[1] + 1, step_y)
            for dx in range(-span_x, span_x + 1, step_x)]
    grid.sort(key=lambda p: (4 * (p[0] - cx) ** 2 + (p[1] - cy) ** 2,
                             abs(p[0] - cx), p[1]))
    return tuple(grid[:attempts])


NPC_CLICK_OFFSETS = _npc_click_offsets()
NPC_CLICK_WAIT = 1.5
NPC_SWEEP_BUDGET = 120.0
NPC_LOST_LIMIT = 6
PURCHASE_TAB_WORD = "Purchase"
PURCHASE_TAB_REF = (128, 67)
REGISTER_TAB_REF = (382, 69)


def layout_is_fitted() -> bool:
    return bool(LAYOUT) and "reference defaults" not in (
        getattr(LAYOUT, "measured_from", "") or "")


def anchor_point(name: str) -> "tuple[int, int] | None":
    if not layout_is_fitted():
        return None
    ref = dict(REF_ANCHORS_ALL).get(name)
    return LAYOUT.point(ref) if ref else None
REGISTER_TAB_WORD = "Register"
TRADE_OPEN_MARKERS = ("Purchase", "Adjust", "Register", "Function")

TRADE_TOP_BAND = (10, 30, 1235, 300)
TRADE_WINDOW_SEARCH = (0, 0, 1700, 700)


def find_npc(
    source: Image.Image | None = None, retries: int = NPC_FIND_RETRIES,
    seen: dict | None = None,
) -> tuple[int, int] | None:
    def excluded(point: tuple[int, int]) -> bool:
        x, y = point
        return any(left <= x <= right and top <= y <= bottom
                   for left, top, right, bottom in NPC_EXCLUDE_ZONES)

    def label_centre(image: Image.Image, region=None) -> tuple[int, int] | None:
        for line in _text_lines(find_words(image, region or NPC_SEARCH_REGION, 25)):
            joined = _normalise("".join(w.text for w in line))
            strict = (NPC_NAME_FRAGMENT in joined
                      and NPC_TITLE_FRAGMENT in joined)
            if not strict:
                name_seen, title_seen = _npc_label_words(line)
                if not (name_seen and title_seen):
                    continue
            window = _minimal_window(line, (NPC_NAME_FRAGMENT,
                                            NPC_TITLE_FRAGMENT))
            if window is None:
                wanted = (NPC_NAME_WORD,) + NPC_TITLE_WORDS
                idx = [i for i, w in enumerate(line)
                       if any(_mentions([_normalise(w.text)], want,
                                        NPC_WORD_SIMILARITY)
                              or want in _normalise(w.text)
                              for want in wanted)]
                if not idx:
                    continue
                window = line[min(idx):max(idx) + 1]
            return _span_centre(window)
        return None

    def look(image):
        for pad in (0, 8):
            box = NPC_SEARCH_REGION if pad == 0 else (
                max(0, NPC_SEARCH_REGION[0] - pad),
                max(0, NPC_SEARCH_REGION[1] - pad),
                NPC_SEARCH_REGION[2] + pad, NPC_SEARCH_REGION[3] + pad)
            got = label_centre(image, box)
            if got is not None and not excluded(got):
                return got
        return None

    for _ in range(retries):
        image = source if source is not None else grab()
        label = look(image)
        if label is not None:
            if seen is not None:
                seen["shot"] = image
            return label
        if source is not None:
            break
        time.sleep(0.4)
    return None


def panel_covers_trade_area(gap: float = 0.12, threshold: float = 2.0) -> bool:
    first = grab().convert("L").crop(TRADE_REGION)
    time.sleep(gap)
    second = grab().convert("L").crop(TRADE_REGION)
    diff = ImageChops.difference(first, second)
    return ImageStat.Stat(diff).mean[0] < threshold


def trade_window_open(source: Image.Image | None = None) -> bool:
    image = source if source is not None else grab()
    return any(find_text(image, marker, TRADE_TOP_BAND)
               for marker in TRADE_OPEN_MARKERS)


def register_tab_open(source: Image.Image | None = None) -> bool:
    image = source if source is not None else grab()
    return find_phrase(image, "Register Item", REGISTER_PANEL) is not None


def shop_session_age() -> float | None:
    if _shop_open_since is None:
        return None
    return time.monotonic() - _shop_open_since


def shop_session_expired() -> bool:
    age = shop_session_age()
    return age is None or age >= SHOP_SESSION_SECONDS


def note_shop_opened() -> None:
    global _shop_open_since
    if _shop_open_since is None:
        _shop_open_since = time.monotonic()


def note_shop_closed() -> None:
    global _shop_open_since
    _shop_open_since = None
    forget_range_view()


def open_shop_from_key(timeout: float = 15.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if trade_window_open():
        say("  the Trade window is already open.")
        return True

    origin = inventory_origin()
    if origin is None and open_inventory(timeout=timeout, verbose=verbose):
        origin = inventory_origin()
    if origin is None:
        say("  the Inventory panel is not open, so the Agent Shop key cannot "
            "be reached.")
        record("premium.no_inventory")
        return False

    was_on = active_inventory_tab(origin=origin)

    def restore_tab() -> None:
        if was_on is None or was_on == PREMIUM_SHOP_KEY_TAB:
            return
        here = inventory_origin()
        if here is not None and select_inventory_tab(was_on, here):
            say(f"  inventory returned to tab {was_on}.")
        else:
            say(f"  WARNING: could not return the inventory to tab {was_on}; "
                f"it is on {PREMIUM_SHOP_KEY_TAB}. Anything clicking slots by "
                f"number will hit the wrong tab.")
            record("premium.tab_not_restored", wanted=was_on)


    if not select_inventory_tab(PREMIUM_SHOP_KEY_TAB, origin):
        say(f"  could not reach inventory tab {PREMIUM_SHOP_KEY_TAB}.")
        record("premium.no_tab", tab=PREMIUM_SHOP_KEY_TAB)
        return False
    time.sleep(0.4)
    origin = inventory_origin() or origin

    row, col = PREMIUM_SHOP_KEY_SLOT
    point = slot_centre_at(origin, row, col)

    here_now = active_inventory_tab(origin=origin)
    if here_now != PREMIUM_SHOP_KEY_TAB:
        say(f"  the Inventory is on tab {here_now}, not tab "
            f"{PREMIUM_SHOP_KEY_TAB} - REFUSING to right-click slot "
            f"({row},{col}), which on that tab is an ordinary item and would "
            f"open a Use Item dialog.")
        record("premium.wrong_tab", wanted=PREMIUM_SHOP_KEY_TAB,
               found=here_now if here_now is not None else "unreadable")
        restore_tab()
        return False

    say(f"  --premium: right-clicking the Agent Shop key at ({row},{col}) "
        f"{point}")
    right_click(*point)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if trade_window_open():
            say("  the Agent Shop is open.")
            record("premium.opened")
            restore_tab()
            return True
        time.sleep(0.4)
    try:
        for _ in range(ESCAPE_ATTEMPTS):
            press_escape()
            time.sleep(0.3)
            if not dialog_present():
                break
        else:
            record("premium.key_opened_dialog")
    except Exception as exc:
        say(f"  (could not clear a stray dialog: {exc})")

    say("  the Agent Shop did not open from the key.")
    record("premium.key_failed")
    restore_tab()
    return False


def open_trade_window(timeout: float = 15.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def wait_until(predicate, limit: float) -> bool:
        deadline = time.monotonic() + limit
        while True:
            if predicate():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    if register_tab_open():
        note_shop_opened()
        return True

    index = 0
    if PREMIUM_ENABLED and not trade_window_open():
        if not open_shop_from_key(timeout=timeout, verbose=verbose):
            return False

    if not trade_window_open():
        seen: dict = {}
        label = find_npc(seen=seen)
        if label is None:
            probe = grab()
            words = sorted(find_words(probe, NPC_SEARCH_REGION, 25),
                           key=lambda w: -w.conf)[:12]
            strongest = ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words)
            record("npc.not_found", probe, region=str(NPC_SEARCH_REGION),
                   words=strongest, trade_open=trade_window_open(probe))
            say("Lady Yekaterina (Agent Shop) is not on screen - walk to "
                "her before running this. Nothing was clicked.")
            say(f"  strongest words where she should be: {strongest}")
            return False
        record("npc.found", seen.get("shot"), centre=str(label))
        say(f"Found 'Lady Yekaterina (Agent Shop)' at {label}; sweeping "
            f"{len(NPC_CLICK_OFFSETS)} points beneath it.")

        opened = False
        index = 0
        lost = 0
        sweep_deadline = time.monotonic() + NPC_SWEEP_BUDGET
        for index, offset in enumerate(NPC_CLICK_OFFSETS, start=1):
            if time.monotonic() >= sweep_deadline:
                say(f"  giving up after {index - 1} attempts "
                    f"({NPC_SWEEP_BUDGET:g}s budget spent).")
                break

            fresh = find_npc(retries=1)
            lost = 0 if fresh else lost + 1
            if lost >= NPC_LOST_LIMIT:
                say(f"  the nameplate has not been visible for {lost} attempts "
                    "- she is out of view, so stopping rather than clicking "
                    "the world blind.")
                break
            label = fresh or label

            point = (label[0] + offset[0], label[1] + offset[1])
            say(f"  try {index}/{len(NPC_CLICK_OFFSETS)}: {point}  "
                f"({offset[0]:+}, {offset[1]:+} from the name)")
            click(*point)
            time.sleep(NPC_CLICK_WAIT)

            if panel_covers_trade_area() and trade_window_open():
                opened = True
                break

        if not opened:
            say(f"The Trade window did not open after {index} attempt(s) "
                "beneath the nameplate. Stand closer so she is fully on "
                "screen, then retry.")
            return False
    record("shop.opened", attempts=index)

    if register_tab_open():
        note_shop_opened()
        return True

    point = LAYOUT.point(REGISTER_TAB_REF) if layout_is_fitted() else None
    if point is None:
        tabs = find_text(grab(), REGISTER_TAB_WORD, TRADE_REGION)
        if not tabs:
            say("Could not find the Register tab.")
            return False
        point = tabs[0].centre
    record("tab.before_register_click", centre=str(point))
    click(*point)
    if not wait_until(register_tab_open, timeout):
        say("The Register tab did not open.")
        return False
    record("tab.register_open")
    note_shop_opened()
    return True


def refresh_table(timeout: float = 20.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = grab()
    record("refresh.before", shot)
    point = anchor_point("Refresh")
    if point is not None:
        pad_x, pad_y = LAYOUT.length(90), LAYOUT.length(26)
        near = (point[0] - pad_x, point[1] - pad_y,
                point[0] + pad_x, point[1] + pad_y)
        if not find_text(shot, "Refresh", near):
            record("refresh.not_where_expected", point=list(point))
            say("  the Refresh button is not where the fit says it is - the "
                "window is shut, moved, or covered. Not clicking blind.")
            point = None
    if point is None:
        buttons = find_text(shot, "Refresh", TRADE_REGION)
        if not buttons:
            record("refresh.no_button", shot)
            say("Refresh button not found - is the Trade window open?")
            return False
        point = buttons[-1].centre

    click(*point)
    time.sleep(0.6)
    if not wait_for_table(max(timeout, 20.0)):
        record("refresh.timeout")
        say("The table did not finish refreshing.")
        return False
    record("refresh.after")
    return True


def find_change_buttons(source: Image.Image | Path | str | None = None) -> list[Word]:
    image = source if source is not None else grab()
    return find_text(image, "Change", TRADE_REGION)


def find_row_buttons(image: Image.Image,
                     words: "list[Word] | None" = None) -> list[Word]:
    if words is None:
        hits: list[Word] = []
        for word in BUTTON_WORDS:
            hits.extend(find_text(image, word, TRADE_REGION))
    else:
        wanted = tuple(w.casefold() for w in BUTTON_WORDS)
        hits = [w for w in words
                if any(t in w.text.casefold() for t in wanted)
                and w.conf >= 40.0]
    if not hits:
        return []

    anchors = [w for w in hits if "change" in w.text.casefold()] or hits
    xs = sorted(w.centre[0] for w in anchors)
    column_x = xs[len(xs) // 2]
    column_half = max(20, LAYOUT.length(45))
    hits = [w for w in hits if abs(w.centre[0] - column_x) <= column_half]

    hits.sort(key=lambda w: w.top)
    deduped: list[Word] = []
    for w in hits:
        if deduped and w.top - deduped[-1].top < max(6, LAYOUT.length(20)):
            continue
        deduped.append(w)
    return deduped


def table_loading(source: Image.Image | Path | str,
                  words: "list | None" = None) -> bool:
    if words is not None:
        return any("waiting" in (w.text or "").casefold()
                   for w in words if w.conf >= 40.0)
    return bool(find_text(source, "Waiting", TRADE_REGION))


SERVER_BUSY_WORD = "responding"
SERVER_LAG_IDLE = 30.0
SERVER_LAG_BUDGET = 600.0
SERVER_LAG_RETRIES = 3


def server_busy(source: "Image.Image | None" = None,
                words: "list | None" = None) -> bool:
    try:
        if words is None:
            words = find_words(source if source is not None else grab(),
                               TRADE_REGION, 40.0)
        return any(SERVER_BUSY_WORD in (w.text or "").casefold()
                   for w in words if w.conf >= 40.0)
    except Exception:
        return False


def wait_out_server_lag(timeout: float = SERVER_LAG_BUDGET,
                        verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not server_busy():
        return True
    say(f"The server is not responding. Idling {SERVER_LAG_IDLE:.0f}s rather "
        f"than reading a table it cannot serve.")
    record("server.not_responding")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(SERVER_LAG_IDLE)
        if not server_busy():
            waited = timeout - (deadline - time.monotonic())
            say(f"  the server is answering again after {waited:.0f}s; "
                f"restarting the step.")
            record("server.recovered", waited=round(waited))
            return True
        say(f"  still not responding; idling another "
            f"{SERVER_LAG_IDLE:.0f}s.")
    say("  the server did not come back; giving the cycle back to the loop.")
    record("server.still_down")
    return False


def wait_for_table(timeout: float = 20.0, poll: float = 1.0,
                   verbose: bool = True) -> bool:
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    announced = False
    while time.monotonic() < deadline:
        if not table_loading(grab()):
            if announced and verbose:
                print(f"  the server answered after "
                      f"{time.monotonic() - started:.0f}s; carrying on.")
            return True
        if not announced:
            announced = True
            if verbose:
                print("  the game is showing 'Waiting for the server "
                      "response' - holding off reading the table until it "
                      "clears, because a table mid-refresh reads every row as "
                      "Register and every count as 0.")
            record("table.server_wait")
        time.sleep(poll)
    if verbose:
        print(f"  the table was still refreshing after {timeout:.0f}s.")
    record("table.server_wait_timeout", waited=round(timeout))
    return False


def _header(words: "list[Word] | None", image: Image.Image, needle: str):
    if words is None:
        hits = find_text(image, needle, TRADE_REGION)
    else:
        low = needle.casefold()
        hits = sorted((w for w in words
                       if low in w.text.casefold() and w.conf >= 40.0),
                      key=lambda w: w.top)
    return hits[0] if hits else None


def name_column(image: Image.Image,
                words: "list[Word] | None" = None) -> tuple[int, int]:
    header = _header(words, image, "Name")
    qty_header = _header(words, image, "QTY")
    names = [header] if header else []
    qtys = [qty_header] if qty_header else []
    if not names or not qtys:
        return NAME_COLUMN
    header, qty = names[0], qtys[0]
    centre = (header.left + header.right) // 2
    half = qty.left - centre
    if half <= 0:
        return NAME_COLUMN
    left, right = (max(0, centre - half + LAYOUT.length(4)),
                   qty.left - LAYOUT.length(6))
    if right - left < LAYOUT.length(40):
        return NAME_COLUMN
    return (left, right)


def price_column(image: Image.Image,
                 words: "list[Word] | None" = None) -> tuple[int, int] | None:
    qty_header = _header(words, image, "QTY")
    status_header = _header(words, image, "Status")
    if qty_header is None or status_header is None:
        return None
    qtys, statuses = [qty_header], [status_header]
    left, right = (qtys[0].right + LAYOUT.length(4),
                   statuses[0].left - LAYOUT.length(4))
    return (left, right) if right > left else None


def status_column(image: Image.Image,
                  words: "list[Word] | None" = None) -> tuple[int, int] | None:
    status_header = _header(words, image, "Status")
    function_header = _header(words, image, "Function")
    if status_header is None or function_header is None:
        return None
    left, right = (status_header.left - LAYOUT.length(6),
                   function_header.left - LAYOUT.length(6))
    return (left, right) if right > left else None


def status_is_complete(text: str) -> bool:
    return "complet" in _normalise(text or "")


def status_on_sale(text: str) -> bool:
    return "sale" in _normalise(text or "")


def await_rows(timeout: float = TABLE_READ_BUDGET, poll: float = 0.5) -> list[Row]:
    try:
        park_cursor()
    except PermissionError:
        pass

    deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
    lag_waits = 0
    while True:
        shot = grab()
        words = find_words(shot, TRADE_REGION, 0.0)
        if server_busy(words=words):
            lag_waits += 1
            if lag_waits > SERVER_LAG_RETRIES:
                say_unreadable = (f"  the server has been unresponsive across "
                                  f"{SERVER_LAG_RETRIES} waits; treating the "
                                  f"table as unreadable for now.")
                print(say_unreadable)
                record("server.gave_up", waits=lag_waits)
                return []
            if not wait_out_server_lag(verbose=True):
                return []
            deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
            time.sleep(poll)
            continue
        if not table_loading(shot, words=words):
            rows = read_rows(shot, words=words)
            if rows:
                return rows
        if time.monotonic() >= deadline:
            return []
        time.sleep(poll)


def read_rows(source: Image.Image | Path | str,
              words: "list | None" = None,
              expect: "int | None" = None) -> list[Row]:
    image = source if isinstance(source, Image.Image) else Image.open(source)

    if words is None:
        words = find_words(image, TRADE_REGION, 0.0)
    if not words:
        return []

    buttons = find_row_buttons(image, words)
    if not buttons:
        return []

    wanted = EXPECTED_ROWS if expect is None else int(expect)
    if len(buttons) != wanted:
        return []

    pitch = LAYOUT.length(REF_ROW_PITCH)
    if len(buttons) > 1:
        gaps = sorted(b.top - a.top for a, b in zip(buttons, buttons[1:]))
        pitch = gaps[len(gaps) // 2]
        if any(gap > pitch * 1.6 for gap in gaps):
            return []

    left, right = name_column(image, words)
    price_bounds = price_column(image, words)
    status_bounds = status_column(image, words)

    def cell(x0: int, y0: int, x1: int, y1: int, min_conf: float = 40.0):
        return [w for w in words
                if w.conf >= min_conf
                and x0 <= w.centre[0] <= x1 and y0 <= w.centre[1] <= y1]

    rows: list[Row] = []
    for i, button in enumerate(buttons, start=1):
        cy = button.centre[1]
        top, bottom = cy - pitch // 2, cy + pitch // 2
        cell_words = [w for w in cell(left, top, right, bottom)
                      if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
        name = " ".join(w.text for line in _text_lines(cell_words) for w in line)

        if not name.strip() and button.text.strip().casefold() != "register":
            retry = [w for w in find_words(image, (left, top, right, bottom), 40.0)
                     if len(w.text.strip()) > 2 or w.conf >= NAME_FRAGMENT_MIN_CONF]
            if retry:
                name = " ".join(w.text for line in _text_lines(retry)
                                for w in line)

        price = None
        if price_bounds:
            cell_lines = _text_lines(
                cell(price_bounds[0], top, price_bounds[1], bottom))
            if cell_lines:
                nearest = min(cell_lines,
                              key=lambda ln: abs(_span_centre(ln)[1] - cy))
                price = _digits("".join(w.text
                                        for w in sorted(nearest,
                                                        key=lambda w: w.left)))

        qty = None
        if price_bounds and price_bounds[0] - (right + 2) >= 8:
            box = (right + 2, top, price_bounds[0] - 2, bottom)
            digits = sorted(cell(*box, QTY_COL_MIN_CONF), key=lambda w: w.left)
            qty = _digits("".join(w.text for w in digits))
            if qty is None:
                qty = read_number(image, box, QTY_COL_MIN_CONF)

        action = button.text.strip().casefold()
        if action == "register" and name.strip():
            if PREMIUM_SLOT_MARKER in _normalise(name):
                name = ""
            else:
                return []
        status = ""
        if status_bounds:
            band = cell(status_bounds[0], top, status_bounds[1], bottom, 30.0)
            status = " ".join(w.text for w in sorted(band, key=lambda w: w.left)
                              ).strip()

        rows.append(Row(
            index=i, name=name.strip() or "(empty)", change=button.centre,
            top=top, bottom=bottom, action=action,
            price=price, qty=qty, status=status,
        ))
    return rows


def dialog_button(
    source: Image.Image | Path | str, word: str, min_conf: float = 40.0,
    words: "list | None" = None
) -> Word | None:
    column_x = LAYOUT.x(REF_FUNCTION_COLUMN_X)
    keep_away = LAYOUT.length(60)
    needle = word.casefold()
    pool = ([w for w in words
             if w.conf >= min_conf and needle in (w.text or "").casefold()]
            if words is not None
            else find_text(source, word, POPUP_REGION, min_conf))
    hits = [w for w in sorted(pool, key=lambda w: w.top)
            if abs(w.centre[0] - column_x) > keep_away
            and w.centre[1] >= DIALOG_BUTTON_MIN_Y]
    return hits[-1] if hits else None


def await_dialog_button(
    word: str, timeout: float = 6.0, poll: float = 0.4, source=None
) -> Word | None:
    if source is not None:
        button = (dialog_button_band(word, source=source)
                  or dialog_button(source, word))
        if button is not None:
            return button

    park_cursor()
    deadline = time.monotonic() + timeout
    while True:
        shot = grab()
        button = dialog_button_band(word, source=shot) or dialog_button(shot, word)
        if button is not None:
            return button
        if time.monotonic() >= deadline:
            break
        time.sleep(poll)
    banded = dialog_button_band(word)
    if banded is not None:
        return banded
    faint = dialog_button(grab(), word, min_conf=15.0)
    if faint is not None:
        return faint
    return dialog_button_band(word)


DIALOG_BUTTON_BANDS = (
    (DIALOG_BUTTON_MIN_X, 820, 1650, 900),
    (DIALOG_BUTTON_MIN_X, 855, 1600, 900),
)


def dialog_button_band(word: str, source=None, min_conf: float = 40.0):
    if source is None:
        park_cursor()
        time.sleep(0.25)
    shot = source if source is not None else grab()
    want = _normalise(word)
    best = None
    for band in DIALOG_BUTTON_BANDS:
        for w in find_words(shot, band, min_conf):
            if _normalise(w.text) != want:
                continue
            if best is None or w.conf > best.conf:
                best = w
        if best is not None:
            return best
    return best


def _mentions(texts: list[str], keyword: str, threshold: float = 0.85) -> bool:
    from difflib import SequenceMatcher

    for text in texts:
        if keyword in text:
            return True
        if abs(len(text) - len(keyword)) > 2:
            continue
        if text in keyword:
            return True
        if SequenceMatcher(None, keyword, text).ratio() >= threshold:
            return True
    return False


def dialog_kind(source: Image.Image | Path | str,
                words: "list | None" = None) -> str | None:
    if words is None:
        words = find_words(source, POPUP_REGION, DIALOG_TEXT_MIN_CONF)
    else:
        words = [w for w in words if w.conf >= DIALOG_TEXT_MIN_CONF]
    texts = [_normalise(w.text) for w in words]
    texts += [_normalise("".join(w.text for w in line))
              for line in _text_lines(words)]
    if _mentions(texts, "receipt"):
        return "receipt"
    if _mentions(texts, "confirmation"):
        return "confirm"
    if _mentions(texts, "extension") or _mentions(texts, "registration"):
        confirm_btn = dialog_button_band(CONFIRM_WORD, source=source)
        register_btn = dialog_button_band(REGISTER_TAB_WORD, source=source)
        if confirm_btn is not None and register_btn is None:
            return "confirm"
        return "extension"
    return None


def dialog_present(source: Image.Image | Path | str | None = None) -> bool:
    shot = source if source is not None else grab()
    words = find_words(shot, POPUP_REGION, DIALOG_TEXT_MIN_CONF)
    if dialog_kind(shot, words=words) is not None:
        return True
    return dialog_button(shot, DISMISS_WORD, words=words) is not None


def confirm_open_dialogs(settle: float = 0.8, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    confirmed = 0
    for step in range(1, MAX_CONFIRM_STEPS + 1):
        button = await_dialog_button(CONFIRM_WORD, timeout=3.0)
        label = CONFIRM_WORD
        if button is None:
            button = await_dialog_button(RECEIPT_WORD, timeout=5.0)
            label = RECEIPT_WORD
        if button is None:
            say(f"Nothing left to confirm after {confirmed} click(s).")
            break
        say(f"Dialog {step}: clicking {label} at {button.centre}")
        click(*button.centre)
        confirmed += 1
        time.sleep(settle)
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not dialog_present():
            return True
        time.sleep(0.3)
    return False


def close_any_dialog(settle: float = 0.7,
                     tries: int = CLOSE_DIALOG_TRIES) -> bool:
    for _ in range(tries):
        button = await_dialog_button(DISMISS_WORD, timeout=3.0)
        if button is None:
            break
        click(*button.centre)
        time.sleep(settle)
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not dialog_present():
            return True
        time.sleep(0.3)
    return False


def _normalise(text: str) -> str:
    return "".join(ch for ch in text.casefold() if ch.isalnum())


_OCR_CONFUSIONS = str.maketrans({"0": "o", "1": "i", "l": "i"})

FUZZY_NAME_THRESHOLD = 0.95
FUZZY_NAME_MARGIN = 0.03
UNMATCHED_NAME_SIMILARITY = 0.70
_COLLECTED_FULLY: set = set()


def note_fully_collected(name: str) -> None:
    key = _canonical(name)
    if key:
        _COLLECTED_FULLY.add(key)


def forget_collected() -> None:
    _COLLECTED_FULLY.clear()


def was_fully_collected(name: str) -> bool:
    return _canonical(name) in _COLLECTED_FULLY
NAME_FRAGMENT_MIN_CONF = 60.0


def _canonical(text: str) -> str:
    return _normalise(text).translate(_OCR_CONFUSIONS)


_FLOOR_LOOKALIKES = str.maketrans({
    "|": "i", "!": "i", "1": "i", "l": "i", "[": "i", "]": "i",
    "/": "i", "\\": "i", "j": "i", ":": "i", ";": "i", "¦": "i",
    "0": "o",
})


def _floor_key(text: str) -> str:
    folded = text.casefold().translate(_FLOOR_LOOKALIKES)
    return "".join(ch for ch in folded if ch.isalnum())


_NAME_TRAILER = re.compile(
    r"\b(use\s*period|duration|grade\s*\d|drop\s*not\s*allowed|register\s*cost"
    r"|item\s*sold|remaining\s*time|sales?\s*price)\b.*",
    re.IGNORECASE | re.DOTALL,
)


def item_name(row_name: str) -> str:
    return _NAME_TRAILER.sub("", row_name).strip(" :-")


def match_rows(rows: list[Row], item: str,
               threshold: float = FUZZY_NAME_THRESHOLD) -> list[Row]:
    from difflib import SequenceMatcher

    needle = _canonical(item)
    if not needle:
        return []

    exact = [r for r in rows if _canonical(r.name) == needle]
    if exact:
        return exact

    scored = []
    for row in rows:
        candidate = _canonical(row.name)
        if candidate:
            scored.append((SequenceMatcher(None, needle, candidate).ratio(), row))
    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best = scored[0]
    best_name = _canonical(best.name)

    runner_up = next((score for score, row in scored[1:]
                      if _canonical(row.name) != best_name), 0.0)

    required = max(threshold, 1.0 - 1.0 / max(len(needle), 1) + 0.005)
    if best_score < required or best_score - runner_up < FUZZY_NAME_MARGIN:
        return []
    return [row for _, row in scored if _canonical(row.name) == best_name]


def match_row(rows: list[Row], item: str,
              threshold: float = FUZZY_NAME_THRESHOLD) -> Row | None:
    found = match_rows(rows, item, threshold)
    return found[0] if len(found) == 1 else None


@dataclass(frozen=True)
class RowRef:
    name: str
    qty: int | None = None
    price: int | None = None
    ordinal: int = 0
    siblings: int = 1

    @classmethod
    def of(cls, row: Row, rows: list[Row] | None = None) -> "RowRef":
        ordinal = 0
        if rows:
            for position, candidate in enumerate(cls._siblings(rows, row)):
                if candidate.index == row.index:
                    ordinal = position
                    break
        pool = cls._siblings(rows, row) if rows else [row]
        return cls(row.name, row.qty, row.price, ordinal, max(1, len(pool)))

    @staticmethod
    def _siblings(rows: list[Row], row: Row) -> list[Row]:
        pool = match_rows(rows, row.name)
        for value, attribute in ((row.qty, "qty"), (row.price, "price")):
            if value is None:
                continue
            narrowed = [r for r in pool if getattr(r, attribute) == value]
            if narrowed:
                pool = narrowed
        return pool


SCROLL_TO_END_NOTCHES = (SHOP_ROW_CAPACITY - SCREEN_ROWS) + 2
MIN_SCROLL_OVERLAP = 3
SCROLL_MATCH_RATIO = 0.6
SCROLL_MATCH_MARGIN = 2
SCROLL_MATCH_MIN_LIVE = 2


def _row_key(row: Row) -> tuple:
    return (row.name, row.price, row.qty, row.action)


def anchor_shift(before: list[Row], after: list[Row]) -> "int | None":
    b = [_row_key(r) for r in before]
    a = [_row_key(r) for r in after]
    if not b or not a:
        return None
    votes = set()
    for i, key in enumerate(b):
        if key[0] == "(empty)":
            continue
        if b.count(key) != 1 or a.count(key) != 1:
            continue
        votes.add(i - a.index(key))
        if len(votes) > 1:
            return None
    if len(votes) != 1:
        return None
    shift = votes.pop()
    return shift if shift >= 0 else None


def measure_shift(before: list[Row], after: list[Row],
                  minimum: int = MIN_SCROLL_OVERLAP,
                  expected: int | None = None) -> int | None:
    b = [_row_key(r) for r in before]
    a = [_row_key(r) for r in after]
    if not b or not a:
        return None

    if b == a:
        if expected and all(r.name == "(empty)" for r in before):
            return expected
        return 0

    candidates = (range(0, expected + 1) if expected is not None
                  else range(-len(b), len(b) + 1))

    fits = []
    for shift in candidates:
        d = -shift
        overlap = [(i, i + d) for i in range(len(b)) if 0 <= i + d < len(a)]
        if len(overlap) >= minimum and all(b[i] == a[j] for i, j in overlap):
            fits.append(shift)
    if len(fits) == 1:
        return fits[0]

    if fits:
        if expected is not None and expected in fits:
            overlap = [i for i in range(len(b))
                       if 0 <= i - expected < len(a)]
            if overlap and all(before[i].name == "(empty)" for i in overlap):
                return expected
        return None

    live_b = [name != "(empty)" for name in (r.name for r in before)]

    scored: list[tuple[int, int]] = []
    for shift in candidates:
        d = -shift
        overlap = [(i, i + d) for i in range(len(b)) if 0 <= i + d < len(a)]
        if len(overlap) < minimum:
            continue
        agree = [(i, j) for i, j in overlap if b[i] == a[j]]
        if len(agree) < SCROLL_MATCH_RATIO * len(overlap):
            continue
        speaking = sum(1 for i, _ in agree if live_b[i])
        want_live = min(SCROLL_MATCH_MIN_LIVE, sum(live_b)) or 1
        if speaking < want_live:
            continue
        scored.append((speaking, shift))
    if not scored:
        return None

    scored.sort(reverse=True)
    best, shift = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    if best - runner_up < SCROLL_MATCH_MARGIN:
        return None
    return shift


ROW_INDEX_LIMIT = SHOP_ROW_CAPACITY - SCREEN_ROWS


def read_top_row(source: "Image.Image | None" = None) -> "Row | None":
    shot = source if source is not None else grab()
    words = find_words(shot, TABLE_HEAD_BAND, 0.0)
    if not words:
        return None
    rows = read_rows(shot, words=words, expect=1)
    return rows[0] if rows else None


def goto_row(index: int, timeout: float = 8.0,
             verbose: bool = True) -> "Row | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not 1 <= index <= ROW_INDEX_LIMIT:
        say(f"  row {index} cannot be placed at the top of the view: only "
            f"rows 1-{ROW_INDEX_LIMIT} can, because the view clamps once the "
            f"last of {SHOP_ROW_CAPACITY} rows reaches the bottom.")
        return None

    per_notch = scroll_rows_per_notch()
    if per_notch is None:
        per_notch = calibrate_scroll(timeout=timeout, verbose=verbose)
    if not per_notch or per_notch <= 0:
        say("  the wheel is not calibrated against the table, so a notch "
            "count cannot be turned into a row. Refusing to guess.")
        return None

    if scroll_to_end(up=True, timeout=timeout, verbose=False,
                     read=False) is None:
        say("  the view could not be driven to the top.")
        return None

    notches = int(round((index - 1) / per_notch))
    if notches:
        scroll_wheel(*SCROLL_POINT, -notches, checked=True)
    row = read_top_row()
    if row is None:
        say(f"  row {index} was scrolled to but could not be read.")
        return None
    say(f"  row {index} is at the top of the view after {notches} notch(es): "
        f"{row.name!r}")
    return row


def step_row(notches: int = 1, verbose: bool = True) -> "Row | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    if notches:
        scroll_wheel(*SCROLL_POINT, -int(notches), checked=True)
    row = read_top_row()
    if row is None:
        say(f"  the top row could not be read after {notches} notch(es).")
    return row


def table_scrollable(verbose: bool = True) -> bool:
    if not (trade_window_open() and panel_covers_trade_area()):
        if verbose:
            print("  the Trade window is not open - refusing to scroll, the "
                  "wheel would zoom the camera instead of moving the "
                  "listings.")
        record("scroll.refused_window_shut")
        return False

    if register_tab_open():
        return True
    if verbose:
        print("  the Trade window is on the Purchase tab - refusing to scroll, "
              "the wheel would move the OFFERS and row 1 would stop meaning "
              "the cheapest one.")
    record("scroll.refused_wrong_tab")
    return False


def scroll_to_end(up: bool, timeout: float = 8.0,
                  verbose: bool = True, read: bool = True) -> "list[Row] | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not table_scrollable(verbose=verbose):
        return None

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre,
                 SCROLL_TO_END_NOTCHES if up else -SCROLL_TO_END_NOTCHES,
                 checked=True)
    park_cursor(settle=TOOLTIP_CLEAR_SECONDS)
    if not read:
        return []
    rows = await_rows(timeout)
    if not rows:
        say("  the table could not be read after scrolling.")
        return None
    return rows


def scroll_one(down: bool, before: list[Row], timeout: float = 8.0,
               verbose: bool = True) -> tuple[list[Row] | None, int | None]:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not table_scrollable(verbose=verbose):
        return None, None

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, -1 if down else 1)
    park_cursor(settle=TOOLTIP_CLEAR_SECONDS)
    after = await_rows(timeout)
    if not after:
        say("  the table could not be read after scrolling.")
        return None, None
    shift = measure_shift(before, after, expected=1)
    if shift is None:
        say("  could not tell how far the view moved - refusing to guess "
            "which listing is which.")
        record("scroll.unmeasured",
               before=" | ".join(f"{r.index}:{r.name}" for r in before[:4]),
               after=" | ".join(f"{r.index}:{r.name}" for r in after[:4]),
               live_before=sum(1 for r in before if r.name != "(empty)"),
               live_after=sum(1 for r in after if r.name != "(empty)"))
        return after, None
    want = 1 if down else -1
    if shift not in (0, want):
        say(f"  one notch moved {shift} rows, expected 0 or {want} - stopping "
            "rather than reinterpreting it.")
        return after, None
    return after, shift


SCROLL_STEP = 7
SCROLL_STEP_FALLBACK = 3
MAX_SCROLL_CHUNKS = 8

BRING_INTO_VIEW_STALE = 2


def scroll_chunk(notches: int, before: list[Row], timeout: float = 8.0,
                 verbose: bool = True) -> tuple[list[Row] | None, int | None]:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not table_scrollable(verbose=verbose):
        return None, None

    centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
              (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
    scroll_wheel(*centre, -abs(notches))
    park_cursor(settle=TOOLTIP_CLEAR_SECONDS)
    after = await_rows(timeout)
    if not after:
        say("  the table could not be read after scrolling.")
        return None, None
    shift = measure_shift(before, after, expected=abs(notches))
    if shift is None:
        say("  could not tell how far the view moved - refusing to guess "
            "which listing is which.")
        record("scroll.unmeasured",
               before=" | ".join(f"{r.index}:{r.name}" for r in before[:4]),
               after=" | ".join(f"{r.index}:{r.name}" for r in after[:4]),
               live_before=sum(1 for r in before if r.name != "(empty)"),
               live_after=sum(1 for r in after if r.name != "(empty)"))
        return after, None
    if not 0 <= shift <= abs(notches):
        say(f"  {abs(notches)} notch(es) moved the view {shift} rows - "
            "stopping rather than reinterpreting it.")
        return after, None
    return after, shift


def bring_into_view(ref: RowRef, timeout: float = 8.0,
                    verbose: bool = True,
                    hint: "int | None" = None,
                    report: "dict | None" = None) -> list[Row] | None:
    def holds(view: list[Row]) -> bool:
        live = [r for r in view if r.action in ("change", "receive")]
        return locate_row(live, ref)[0] is not None

    positional = ref.siblings > 1 and hint is not None
    if positional and verbose:
        print(f"  {ref.name!r} has {ref.siblings} indistinguishable "
              f"sibling(s); walking from the top so row {hint} can be "
              f"identified by position rather than by identity.")

    if not positional:
        here = await_rows(timeout)
        if here and holds(here):
            if verbose:
                print("  the listing is already on screen; no scrolling "
                      "needed.")
            if report is not None:
                report["top_index"] = None
            return here

    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    if hint is not None and rows and not positional:
        jump = hint - len(rows)
        if jump > 0 and table_scrollable(verbose=False):
            if verbose:
                print(f"  the shop read put {ref.name!r} at row {hint}; "
                      f"scrolling {jump} row(s) straight there instead of "
                      f"stepping.")
            centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
                      (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
            scroll_wheel(*centre, -jump)
            park_cursor(settle=TOOLTIP_CLEAR_SECONDS)
            landed = await_rows(timeout)
            if landed and holds(landed):
                return landed
            if verbose:
                print("  the jump did not land on it; walking from the top.")
            record("bring_into_view.jump_missed", item=ref.name, hint=hint)
            rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
            if not rows:
                return None

    steps = 0
    unchanged = 0
    previous = [_row_key(r) for r in rows]
    walked = 0
    while steps < MAX_SCROLL_CHUNKS * SCROLL_STEP:
        steps += 1
        if report is not None:
            report["top_index"] = walked + 1
        if positional and hint is not None:
            if walked + 1 <= hint <= walked + len(rows):
                if verbose:
                    print(f"  walked {walked} row(s) down; row {hint} is "
                          f"screen row {hint - walked} of this view.")
                return rows
        elif holds(rows):
            if verbose and walked:
                print(f"  walked {walked} row(s) down in {steps - 1} step(s) "
                      f"to reach {ref.name!r}.")
            return rows
        step = informative_step(rows, SCROLL_STEP)
        after, shift = scroll_chunk(step, rows, timeout=timeout,
                                    verbose=verbose)
        if after is None or shift is None:
            return None
        rows = after
        walked += shift

        current = [_row_key(r) for r in rows]
        if current == previous:
            unchanged += 1
            if unchanged >= BRING_INTO_VIEW_STALE:
                if verbose:
                    print(f"  the view stopped changing after {steps} step(s) "
                          "- treating this as the bottom of the shop.")
                break
        else:
            unchanged = 0
        previous = current

        if shift == 0:
            break
    return rows


_WALK_COUNT = 0


def note_walk(what: str, rows: "int | None" = None) -> int:
    global _WALK_COUNT
    _WALK_COUNT += 1
    scope = f"rows 1-{rows}" if rows else "the whole shop"
    print(f"  [TABLE WALK #{_WALK_COUNT} this cycle] {what}, {scope}.")
    record("table.walk", n=_WALK_COUNT, why=what, rows=rows or 0)
    return _WALK_COUNT


def reset_walk_count() -> None:
    global _WALK_COUNT
    _WALK_COUNT = 0


def walks_this_cycle() -> int:
    return _WALK_COUNT


def enumerate_listings(timeout: float = 8.0,
                       verbose: bool = True,
                       stop_after: "int | None" = None
                       ) -> list[tuple[int, Row]] | None:
    def say(message: str) -> None:
        if verbose:
            print(message)

    try:
        caller = sys._getframe(1).f_code.co_name
    except Exception:
        caller = "?"
    note_walk(f"{caller} is reading the table", stop_after)

    try:
        calibrate_scroll(timeout=timeout, verbose=True)

        for step in (SCROLL_STEP, SCROLL_STEP_FALLBACK):
            found = _enumerate_at_step(step, timeout, verbose, say,
                                       stop_after=stop_after)
            if found is not None:
                return found
            if step != SCROLL_STEP_FALLBACK:
                say(f"  retrying the sweep {SCROLL_STEP_FALLBACK} row(s) at a "
                    f"time, so more rows overlap to match on.")
        return None
    finally:
        try:
            scroll_to_end(up=True, timeout=timeout, verbose=False)
        except Exception:
            pass


_SCROLL_ROWS_PER_NOTCH: "float | None" = None


def forget_scroll_calibration() -> None:
    global _SCROLL_ROWS_PER_NOTCH
    _SCROLL_ROWS_PER_NOTCH = None


def scroll_rows_per_notch() -> "float | None":
    return _SCROLL_ROWS_PER_NOTCH


def calibrate_scroll(timeout: float = 8.0, verbose: bool = True) -> "float | None":
    global _SCROLL_ROWS_PER_NOTCH

    def say(message: str) -> None:
        if verbose:
            print(message)

    if _SCROLL_ROWS_PER_NOTCH is not None:
        return _SCROLL_ROWS_PER_NOTCH
    probe = 3
    try:
        if not table_scrollable(verbose=False):
            return None
        before = scroll_to_end(up=True, timeout=timeout, verbose=False)
        if not before:
            return None
        for attempt in range(3):
            centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
                      (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
            scroll_wheel(*centre, -probe)
            park_cursor(settle=TOOLTIP_CLEAR_SECONDS)
            after = await_rows(timeout)
            if not after:
                return None
            shift = anchor_shift(before, after)
            if shift is None:
                shift = measure_shift(before, after, expected=probe)
            if shift:
                _SCROLL_ROWS_PER_NOTCH = float(shift) / probe
                say(f"  wheel calibrated: {probe} notch(es) moved {shift} "
                    f"row(s), so one notch = {_SCROLL_ROWS_PER_NOTCH:g} row(s).")
                record("scroll.calibrated",
                       rows_per_notch=_SCROLL_ROWS_PER_NOTCH,
                       probe=probe, attempt=attempt + 1)
                scroll_to_end(up=True, timeout=timeout, verbose=False)
                return _SCROLL_ROWS_PER_NOTCH
            before = after
            say(f"  no row on this screen is distinctive enough to measure "
                f"against; probing {probe} row(s) further down.")
        say("  THE WHEEL COULD NOT BE CALIBRATED - falling back to measuring "
            "every step, which is slow. Expect full walks this cycle.")
        record("scroll.calibration.failed", probe=probe)
        scroll_to_end(up=True, timeout=timeout, verbose=False)
        return None
    except Exception as exc:
        say(f"  could not calibrate the wheel ({exc}); measuring per step.")
        return None


def informative_step(rows: list[Row], want: int) -> int:
    n = len(rows)
    ceiling = min(want, max(1, n - MIN_SCROLL_OVERLAP))
    for step in range(ceiling, 0, -1):
        overlap = rows[step:]
        if len(overlap) < MIN_SCROLL_OVERLAP:
            continue
        keys = [_row_key(r) for r in overlap if r.name != "(empty)"]
        distinctive = sum(1 for k in keys if keys.count(k) == 1)
        if distinctive >= SCROLL_MATCH_MIN_LIVE:
            return step
    return 1


def _enumerate_at_step(step: int, timeout: float, verbose: bool,
                       say, stop_after: "int | None" = None
                       ) -> list[tuple[int, Row]] | None:
    tail = scroll_to_end(up=False, timeout=timeout, verbose=verbose)
    if not tail:
        return None
    tail_keys = [_row_key(r) for r in tail]

    rows = scroll_to_end(up=True, timeout=timeout, verbose=verbose)
    if not rows:
        return None

    found: list[tuple[int, Row]] = [(i + 1, r) for i, r in enumerate(rows)]
    top = 1
    if [_row_key(r) for r in rows] == tail_keys:
        if len(set(tail_keys)) < 2:
            say("  every row on screen reads alike, so the top and the bottom "
                "of the shop cannot be told apart - refusing to report this "
                "as the whole shop.")
            return None
        say(f"  {len(found)} listing(s) in the shop (all on the first screen)")
        return found
    steps = 0
    barren = 0
    while steps < MAX_SCROLL_CHUNKS * SCROLL_STEP:
        if stop_after is not None and top + EXPECTED_ROWS - 1 >= stop_after:
            say(f"  rows 1-{stop_after} are covered; stopping here rather "
                f"than walking the rest of the shop.")
            break
        steps += 1
        this_step = informative_step(rows, step)

        per_notch = scroll_rows_per_notch()
        if per_notch and this_step < step:
            say(f"  stepping the full {step} rather than {this_step}: the "
                f"wheel is calibrated at {per_notch:g} row(s) per notch, so "
                f"the shift does not have to be read off the rows.")
            this_step = step
        elif this_step != step:
            say(f"  stepping {this_step} instead of {step} - the last "
                f"{len(rows) - this_step} row(s) of this screen include "
                f"something nameable to match on.")
        after, shift = scroll_chunk(this_step, rows, timeout=timeout,
                                    verbose=verbose)
        if after is None or shift is None:
            return None
        top = min(top + shift, max(1, SHOP_ROW_CAPACITY - len(after) + 1))
        rows = after
        was_named = sum(1 for _, r in found if r.name != "(empty)")
        for offset, row in enumerate(rows):
            index = top + offset
            if index > len(found):
                found.append((index, row))
        grew = sum(1 for _, r in found if r.name != "(empty)") > was_named

        barren = 0 if grew else barren + 1
        at_tail = [_row_key(r) for r in rows] == tail_keys
        if at_tail and (len(set(tail_keys)) >= 2 or barren >= 3):
            break
    else:
        say(f"  still scrolling after {steps} steps - refusing to continue.")
        return None

    say(f"  {len(found)} listing(s) in the shop "
        f"({len(found) - EXPECTED_ROWS} beyond the first screen)")
    return found


def listing_family(rows: list[Row], name: str,
                   price: int | None) -> list[Row]:
    pool = [r for r in match_rows(rows, name)
            if r.action in ("change", "receive")]
    if price is not None:
        pool = [r for r in pool if r.price == price]
    return pool


def family_quantities(pool: list[Row]) -> list:
    return sorted((r.qty for r in pool), key=lambda q: (q is None, q))


def collect_delta(before: list, after: list) -> tuple[list, list]:
    unmatched = list(before)
    gained = []
    for value in after:
        if value in unmatched:
            unmatched.remove(value)
        else:
            gained.append(value)
    return unmatched, gained


def locate_row(rows: list[Row], ref: RowRef,
               strict: bool = False) -> tuple[Row | None, str]:
    same = match_rows(rows, ref.name)
    if not same:
        from difflib import SequenceMatcher

        needle = _canonical(ref.name)
        near = max((SequenceMatcher(None, needle, _canonical(r.name)).ratio()
                    for r in rows if _canonical(r.name)), default=0.0)
        if near >= UNMATCHED_NAME_SIMILARITY:
            return None, "unmatched"
        return None, "missing"
    if len(same) == 1:
        return same[0], ""

    pool = same
    for attribute, value in (("qty", ref.qty), ("price", ref.price)):
        if value is None:
            continue
        narrowed = [r for r in pool if getattr(r, attribute) == value]
        if len(narrowed) == 1:
            return narrowed[0], ""
        if narrowed:
            pool = narrowed

    if strict:
        return None, "ambiguous"

    chosen = pool[ref.ordinal] if 0 <= ref.ordinal < len(pool) else pool[0]
    priced = f"at {ref.price:,} Alz" if ref.price is not None else "price unread"
    return chosen, (f"{len(pool)} rows are identical ({ref.name!r} x{ref.qty} "
                    f"{priced}); taking row {chosen.index} by position")


SLOT_ONE_OFFSET = (-261, 120)
SLOT_PITCH = (73.9, 74.1)
GRID_SIZE = 8

REGISTER_PANEL = (10, 120, 275, 1040)
PRICE_ROWS = (70, 460, 260, 530)
PRICE_FIELD = (40, 545, 204, 573)
QTY_FIELD = (40, 634, 226, 667)
NET_SALES_ROWS = (30, 700, 265, 800)
SHOP_SLOT = (144, 290)
SHOP_SLOT_BOX = (30, 179, 256, 399)
SHOP_SLOT_STDEV = 20.0
QTY_INPUT = (90, 651)
QTY_MIN_CONF = 15.0
LOAD_ATTEMPTS = 5


def wants_max_quantity(name: str) -> bool:
    lowered = name.casefold()
    if any(token in lowered for token in NO_MAX_QUANTITY_ITEMS):
        return False
    return MAXIMISE_ALL_QUANTITIES
PRICE_TOP_Y = 477
PRICE_BOTTOM_Y = 513
PRICE_ROW_Y_TOL = 14

FLOOR_NAME_SIMILARITY = 0.75
FLOOR_TOKEN_MIN_SIMILARITY = 0.40
FLOOR_LENGTH_RATIO = 0.0
FLOOR_MATCH_MARGIN = 0.05

ESCAPE_ATTEMPTS = 3

RELISTED = "relisted"
SOLD_OUT = "sold_out"
FAILED = "failed"


def choose_price(
    suggested: int,
    price_floor: int = 0,
    floor_price: int | None = None,
    absolute_floor: int = 0,
) -> tuple[int, str]:
    if suggested <= 0:
        if floor_price and floor_price >= MIN_PLAUSIBLE_PRICE:
            return max(floor_price, absolute_floor), \
                f"no market price; keeping the previous {floor_price:,}"
        return max(FALLBACK_PRICE, absolute_floor), \
            "no market price and no previous price; using the fallback"

    if price_floor and suggested < price_floor:
        raise Aborted(
            f"suggested {suggested:,} is below the --floor {price_floor:,}"
        )

    relative = 0
    if floor_price and floor_price >= MIN_PLAUSIBLE_PRICE:
        if absolute_floor:
            relative = -(-floor_price * int(RELATIVE_PRICE_FLOOR * 100) // 100)
        else:
            relative = floor_price

    guard = max(absolute_floor, relative)
    if guard and suggested < guard:
        if guard == absolute_floor and absolute_floor >= relative:
            return absolute_floor, (
                f"market {suggested:,} is below the {absolute_floor:,} floor "
                "for this item; listing at the floor"
            )
        return relative, (
            f"market {suggested:,} is more than "
            f"{100 - int(RELATIVE_PRICE_FLOOR * 100)}% below the listed "
            f"{floor_price:,}; listing at {relative:,}"
        )

    return suggested, ""


def set_behind(core_name: str) -> str:
    slot = favourite_for(core_name)
    if slot is None:
        return ""
    partner = favourite_set_slot(slot)
    return FAVOURITE_SLOTS.get(partner, "") if partner else ""


def core_behind(set_name: str) -> str:
    slot = favourite_for(set_name)
    if slot is None:
        return ""
    core_slot = slot - 1
    if favourite_set_slot(core_slot) != slot:
        return ""
    return FAVOURITE_SLOTS.get(core_slot, "")


def run_id() -> str:
    return _RUN_STARTED_AT.isoformat(timespec="seconds")


def purchase_cost_basis(name: str) -> int:
    wanted = set_behind(name)
    if not wanted:
        return 0
    conn = sales_db()
    if conn is None:
        return 0
    try:
        rows = conn.execute(
            "SELECT item, price, qty FROM purchases "
            "WHERE price > 0 AND qty > 0 AND run = ?",
            (run_id(),)).fetchall()
    except Exception:
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass

    key = _floor_key(item_name(wanted))
    spent = held = 0
    for item, price, qty in rows:
        if _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item))) == key:
            spent += int(price)
            held += int(qty)
    if not held:
        return 0
    return -(-spent // held)


def effective_floor(catalogue: int, reason: str,
                    cost_floor: int = 0) -> tuple[int, str]:
    if cost_floor > catalogue:
        return cost_floor, "what its Cores cost"
    return catalogue, reason


def listing_floor(name: str) -> tuple[int, str]:
    catalogue = item_price_floor(name)
    if not COST_FLOOR_ON_RELIST:
        return catalogue, "the floor set for this item"
    cost = purchase_cost_basis(name)
    if cost > catalogue:
        return cost, "what its Sets were bought for"
    return catalogue, "the floor set for this item"


def item_price_floor(name: str) -> int:
    from difflib import SequenceMatcher

    key = _floor_key(item_name(name))
    if not key:
        return 0

    candidates: list[tuple[float, int]] = []
    for token, catalogue, floor in ITEM_PRICE_FLOORS:
        reference = _floor_key(catalogue)
        if not reference:
            continue
        ratio = max(
            SequenceMatcher(None, reference, key).ratio(),
            SequenceMatcher(None, reference, key[:len(reference)]).ratio(),
        )
        token_hit = _floor_key(token) in key
        long_enough = len(key) >= len(reference) * FLOOR_LENGTH_RATIO
        if (ratio >= FLOOR_NAME_SIMILARITY and long_enough) or (
                token_hit and ratio >= FLOOR_TOKEN_MIN_SIMILARITY):
            candidates.append((ratio, floor, reference))
    if not candidates:
        return 0

    exact = [floor for _, floor, reference in candidates if key == reference]
    if exact:
        return max(exact)

    prefix_related = [
        floor for _, floor, reference in candidates
        if any(other != reference
               and (other.startswith(reference) or reference.startswith(other))
               for _, _, other in candidates)
    ]
    if len(prefix_related) >= 2:
        return max(prefix_related)

    candidates.sort(key=lambda c: (-c[0], -c[1]))
    best_ratio, best_floor, _ = candidates[0]
    rivals = [floor for ratio, floor, _ in candidates[1:]
              if best_ratio - ratio < FLOOR_MATCH_MARGIN]
    if rivals:
        return max([best_floor] + rivals)
    return best_floor


def strictest_price_floor() -> int:
    return max((floor for *_, floor in ITEM_PRICE_FLOORS), default=0)
PANEL_RADIO_X = 39


INVENTORY_TITLE_REGION = (1400, 100, 2560, 300)
ALZ_TO_TITLE = (-241, -718)


def inventory_origin(
    source: Image.Image | None = None,
    retries: int = INVENTORY_ORIGIN_RETRIES,
) -> tuple[int, int] | None:
    for _ in range(retries):
        image = source if source is not None else grab()

        box = find_alz(image)
        if box:
            return (box[2] + ALZ_TO_TITLE[0], box[1] + ALZ_TO_TITLE[1])

        centre = find_phrase(image, "Inventory", INVENTORY_TITLE_REGION)
        if centre is not None:
            return centre

        if source is not None:
            break
        time.sleep(0.4)
    return None


def _point_in_inventory_grid(x: int, y: int) -> bool:
    origin = inventory_origin()
    if origin is None:
        return False
    first = slot_centre_at(origin, 1, 1)
    last = slot_centre_at(origin, GRID_SIZE, GRID_SIZE)
    half_w = SLOT_PITCH[0] / 2
    half_h = SLOT_PITCH[1] / 2
    return (first[0] - half_w <= x <= last[0] + half_w
            and first[1] - half_h <= y <= last[1] + half_h)


def slot_centre_at(origin: tuple[int, int], row: int, col: int) -> tuple[int, int]:
    if not (1 <= row <= GRID_SIZE and 1 <= col <= GRID_SIZE):
        raise ValueError(f"slot ({row},{col}) is outside the {GRID_SIZE}x{GRID_SIZE} grid")
    tx, ty = origin
    return (
        round(tx + SLOT_ONE_OFFSET[0] + SLOT_PITCH[0] * (col - 1)),
        round(ty + SLOT_ONE_OFFSET[1] + SLOT_PITCH[1] * (row - 1)),
    )


def slot_centre(row: int, col: int, source: Image.Image | None = None) -> tuple[int, int]:
    origin = inventory_origin(source)
    if origin is None:
        raise Aborted("could not find the Inventory panel - is it open?")
    return slot_centre_at(origin, row, col)


TAB_ONE_OFFSET = (-281, 52)
TAB_PITCH = 69.2
TAB_COUNT = 8
TAB_ACTIVE_MARGIN = 6.0
SLOT_OCCUPIED_STDEV = 8.0

SLOT_INSET = 26

TAB_SAMPLE_HALF_W = 22
TAB_SAMPLE_BAND = (-25, -15)
SLOT_CHANGE_MIN = 6.0
SLOT_CHANGE_MARGIN = 2.0


def inventory_cells(
    image: Image.Image, origin: tuple[int, int]
) -> dict[tuple[int, int], Image.Image]:
    cells = {}
    for r in range(1, GRID_SIZE + 1):
        for c in range(1, GRID_SIZE + 1):
            cx, cy = slot_centre_at(origin, r, c)
            box = (max(0, cx - SLOT_INSET), max(0, cy - SLOT_INSET),
                   min(image.width, cx + SLOT_INSET),
                   min(image.height, cy + SLOT_INSET))
            cells[(r, c)] = image.crop(box).convert("L")
    return cells


def occupied_slots(
    image: Image.Image, origin: tuple[int, int]
) -> list[tuple[int, int]]:
    found = []
    for key, cell in inventory_cells(image, origin).items():
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        mean = sum(data) / len(data)
        stdev = (sum((p - mean) ** 2 for p in data) / len(data)) ** 0.5
        if stdev >= SLOT_OCCUPIED_STDEV:
            found.append(key)
    return sorted(found)


def require_empty_work_tab(verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    origin = inventory_origin()
    if origin is None:
        record("worktab.no_panel")
        say("The Inventory panel is not visible - open it and rerun.")
        return False

    if not select_inventory_tab(WORK_TAB, origin):
        record("worktab.tab_switch_failed", want=WORK_TAB)
        say(f"Could not switch to inventory tab {WORK_TAB}.")
        return False

    park_cursor()
    occupied = occupied_slots(grab(), origin)
    if occupied and carried_total() > 0:
        say(f"Inventory tab {WORK_TAB} holds {len(occupied)} slot(s), and "
            f"{carried_total()} Set(s) are on the books as bought and not yet "
            f"listed. Not relisting on top of them: the cancelled-item diff "
            f"cannot tell which slots came back while the tab has other "
            f"things in it. The next resupply should convert and list them.")
        record("worktab.carrying", tab=WORK_TAB, occupied=len(occupied),
               carried=carried_total())
        return False
    if occupied:
        where = ", ".join(f"{r},{c}" for r, c in occupied[:12])
        more = f" (+{len(occupied) - 12} more)" if len(occupied) > 12 else ""
        record("worktab.not_empty", tab=WORK_TAB, occupied=len(occupied),
               slots=", ".join(f"{r},{c}" for r, c in occupied[:12]))
        say(f"Inventory tab {WORK_TAB} is not empty - {len(occupied)} slot(s) "
            f"in use: {where}{more}.\n"
            "Clear it before running: cancelled items land here, and leftover "
            "items make it impossible to tell which ones came back.")
        return False

    if carried_total() > 0:
        stale = {slot: carried_sets(slot) for slot in list(_CARRIED_SETS)
                 if carried_sets(slot) > 0}
        say(f"Inventory tab {WORK_TAB} is empty, but "
            f"{carried_total()} Set(s) were on the books as carried: "
            + ", ".join(f"{FAVOURITE_SLOTS.get(s, s)} {n}"
                        for s, n in sorted(stale.items()))
            + ". The bag is the truth, so the books are cleared -- the next "
              "restock buys instead of resuming work that is not there.")
        record("worktab.carry_cleared", tab=WORK_TAB,
               cleared=carried_total(),
               slots=", ".join(f"{s}:{n}" for s, n in sorted(stale.items())))
        for slot in list(stale):
            note_carried_sets(slot, 0)

    say(f"Inventory tab {WORK_TAB} is empty.")
    return True


def money(value: int | None, blank: str = "-") -> str:
    return blank if value is None else format(value, ",")


SALES: list[dict] = []

PURCHASES: list[dict] = []

SALES_DB = Path(os.environ.get("CABAL_SALES_DB") or (SCRIPT_DIR / "sales.db"))
_sales_db_ready = False


def sales_db() -> "sqlite3.Connection | None":
    global _sales_db_ready
    try:
        conn = sqlite3.connect(SALES_DB, timeout=5.0)
        if not _sales_db_ready:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sales (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    at       TEXT    NOT NULL,
                    run      TEXT,
                    item     TEXT    NOT NULL,
                    price    INTEGER,
                    proceeds INTEGER,
                    qty      INTEGER,
                    note     TEXT
                );
                CREATE INDEX IF NOT EXISTS sales_at  ON sales (at);
                CREATE INDEX IF NOT EXISTS sales_run ON sales (run);

                -- Purchases live in their own table rather than as a signed
                -- row in `sales`. A separate table needs no migration of the
                -- rows already there, and the two are genuinely different
                -- shapes: a sale has proceeds that may be unmeasurable, a
                -- purchase has a spend verified against the balance.
                CREATE TABLE IF NOT EXISTS purchases (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    at    TEXT    NOT NULL,
                    run   TEXT,
                    item  TEXT    NOT NULL,
                    price INTEGER,
                    spend INTEGER,
                    qty   INTEGER,
                    note  TEXT
                );
                CREATE INDEX IF NOT EXISTS purchases_at  ON purchases (at);
                CREATE INDEX IF NOT EXISTS purchases_run ON purchases (run);

                -- What this script put ON the market, and how much of it.
                --
                -- Needed because a sale is measured by the balance moving, and
                -- the only bound on how large that move could legitimately be
                -- is how much the listing held. The TABLE cannot supply it:
                -- the QTY column shows what is STILL on sale, so after a
                -- partial sale it is the remainder, not the original. Using it
                -- as the ceiling rejected every partial sale on the books.
                CREATE TABLE IF NOT EXISTS registrations (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    at    TEXT    NOT NULL,
                    run   TEXT,
                    item  TEXT    NOT NULL,
                    price INTEGER,
                    qty   INTEGER
                );
                CREATE INDEX IF NOT EXISTS reg_item ON registrations (item);

                -- One row per chaos bundle currently on the board, holding
                -- what ITS Cores cost. Separate from `purchases` because that
                -- table answers "what was paid for this ITEM overall", which
                -- is an average across the whole holding -- and the whole
                -- point here is that two bundles of the identical item have
                -- different costs and must not share a floor.
                --
                -- Keyed by listed_price rather than by row or quantity: every
                -- bundle has the same name and the same quantity K, and row
                -- numbers shift on any cancel or register, so the price each
                -- was listed at is the only thing that tells two apart. It is
                -- rewritten on every relist so it tracks the row.
                -- `run` scopes every read to the launch that wrote the row.
                -- Without it, lots from earlier runs stay outstanding forever:
                -- they are only ever deleted when a sale retires one, so
                -- anything that sold while the script was off, or was
                -- cancelled by hand, is inherited as though it were still on
                -- the board. On 2026-08-16 that was 14 rows going back six
                -- days, against a board the same run had just counted as
                -- empty -- and the dearest of them set the relist floor.
                CREATE TABLE IF NOT EXISTS chaos_lots (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_cost    INTEGER NOT NULL,
                    listed_price INTEGER NOT NULL,
                    qty          INTEGER NOT NULL,
                    created      TEXT    NOT NULL,
                    run          TEXT
                );
                CREATE INDEX IF NOT EXISTS chaos_lots_price
                    ON chaos_lots (listed_price);
                CREATE INDEX IF NOT EXISTS chaos_lots_run
                    ON chaos_lots (run);

                """
            )
            have = {r[1] for r in conn.execute("PRAGMA table_info(chaos_lots)")}
            if "run" not in have:
                conn.execute("ALTER TABLE chaos_lots ADD COLUMN run TEXT")
            conn.commit()
            _sales_db_ready = True
        return conn
    except Exception:
        return None


def record_sale_row(item: str, price: int | None, proceeds: int | None,
                    qty: int | None, note: str = "") -> bool:
    conn = sales_db()
    if conn is None:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT INTO sales (at, run, item, price, proceeds, qty, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 _RUN_STARTED_AT.isoformat(timespec="seconds"),
                 item, price, proceeds, qty, note or None),
            )
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


SALES_REPORT_HOURS = 24.0
SALES_REPORT_LIMIT = 500
SALES_QUERY_LIMIT = 2000


def sales_since(hours: "float | None" = SALES_REPORT_HOURS,
                limit: int = SALES_REPORT_LIMIT) -> list[tuple]:
    conn = sales_db()
    if conn is None:
        return []
    try:
        if hours is None:
            rows = conn.execute(
                "SELECT at, item, price, proceeds, qty, note FROM sales"
                " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            cutoff = datetime.fromtimestamp(
                time.time() - hours * 3600).isoformat(timespec="seconds")
            rows = conn.execute(
                "SELECT at, item, price, proceeds, qty, note FROM sales"
                " WHERE at >= ? ORDER BY id DESC LIMIT ?",
                (cutoff, limit)).fetchall()
        return list(rows)
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def record_purchase_row(item: str, price: int | None, spend: int | None,
                        qty: int | None, note: str = "") -> bool:
    conn = sales_db()
    if conn is None:
        return False
    try:
        with conn:
            conn.execute(
                "INSERT INTO purchases (at, run, item, price, spend, qty, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 _RUN_STARTED_AT.isoformat(timespec="seconds"),
                 item, price, spend, qty, note or None))
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def note_purchase(item: str, price: int | None, spend: int | None,
                  qty: int | None, note: str = "") -> None:
    PURCHASES.append({"item": item, "price": price, "spend": spend,
                      "qty": qty})
    try:
        stored = record_purchase_row(item, price, spend, qty, note)
    except Exception:
        stored = False
    try:
        record("buy.recorded", item=item, price=price, spend=spend, qty=qty,
               stored=stored)
    except Exception:
        pass


def note_sale(item: str, price: int | None, proceeds: int | None,
              note: str = "") -> None:
    qty = None
    if proceeds and price and proceeds > 0 and price > 0:
        if proceeds % price == 0:
            qty = proceeds // price
    SALES.append({"item": item, "price": price, "proceeds": proceeds,
                  "qty": qty})
    try:
        stored = record_sale_row(item, price, proceeds, qty, note)
    except Exception:
        stored = False
    try:
        record("sale.collected", item=item, price=price, proceeds=proceeds,
               qty=qty, stored=stored)
    except Exception:
        pass


def note_registration(item: str, price: int | None, qty: int | None) -> None:
    if not item or not price or not qty:
        return
    conn = sales_db()
    if conn is None:
        return
    try:
        with conn:
            conn.execute(
                "INSERT INTO registrations (at, run, item, price, qty)"
                " VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"),
                 _RUN_STARTED_AT.isoformat(timespec="seconds"),
                 item, int(price), int(qty)))
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def registered_qty(item: str, price: int | None) -> "int | None":
    if not item or not price:
        return None
    wanted = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", item)))
    if not wanted:
        return None
    conn = sales_db()
    if conn is None:
        return None
    try:
        best = None
        for name, qty in conn.execute(
                "SELECT item, qty FROM registrations WHERE price = ? "
                "AND qty > 0 AND run = ?", (int(price), run_id())):
            if _floor_key(item_name(_PACK_ANYWHERE.sub(" ", name))) != wanted:
                continue
            if best is None or int(qty) > best:
                best = int(qty)
        return best
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sale_rejection(proceeds: "int | None", price: "int | None",
                   still_listed: "int | None",
                   listed_units: "int | None" = None) -> str:
    if not proceeds or proceeds <= 0:
        return ""
    if not price or price <= 0:
        return ""
    if proceeds % price:
        return (f"{proceeds:,} is not a whole number of units at "
                f"{price:,} each ({proceeds / price:.2f})")
    units = proceeds // price
    bound = max(max(0, still_listed or 0), listed_units or 0)
    if bound <= 0:
        bound = SET_STACK_MAX
    if units > bound:
        return (f"{proceeds:,} is {units:,} units at {price:,}, more than the "
                f"{bound:,} this listing could have held")
    return ""


def all_time_totals() -> "tuple[int, int, int, int, int] | None":
    conn = sales_db()
    if conn is None:
        return None
    try:
        sales_n, proceeds = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(proceeds), 0) FROM sales").fetchone()
        buys_n, = conn.execute(
            "SELECT COUNT(*) FROM purchases WHERE qty > 0").fetchone()
        spend, = conn.execute(
            "SELECT COALESCE(SUM(spend), 0) FROM purchases "
            "WHERE qty > 0").fetchone()
        fees, = conn.execute(
            "SELECT COALESCE(SUM(spend), 0) FROM purchases "
            "WHERE qty <= 0").fetchone()
        return int(sales_n), int(proceeds), int(buys_n), int(spend), int(fees)
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def costed_sales(run: "str | None" = None,
                 all_runs: bool = False) -> "tuple[list, dict]":
    conn = sales_db()
    if conn is None:
        return [], {}
    try:
        def key(name: str) -> str:
            return _floor_key(item_name(_PACK_ANYWHERE.sub(" ", name or "")))

        if not all_runs and not run:
            run = run_id()

        lots: dict = {}
        if run:
            rows = conn.execute(
                "SELECT at, item, price, qty FROM purchases "
                "WHERE qty > 0 AND run = ? ORDER BY at", (run,))
        else:
            rows = conn.execute(
                "SELECT at, item, price, qty FROM purchases "
                "WHERE qty > 0 ORDER BY at")
        for at, item, price, qty in rows:
            k = key(sells_as(item))
            if not k:
                continue
            lots.setdefault(k, []).append(
                [at, int(qty), int(price or 0) / max(1, int(qty))])

        out = []
        if run:
            sales = conn.execute(
                "SELECT at, item, qty, proceeds FROM sales "
                "WHERE qty > 0 AND proceeds > 0 AND at >= ? ORDER BY at", (run,))
        else:
            sales = conn.execute(
                "SELECT at, item, qty, proceeds FROM sales "
                "WHERE qty > 0 AND proceeds > 0 ORDER BY at")
        for at, item, qty, proceeds in sales:
            k = key(item)
            units = int(qty) * max(1, pack_size(item))
            left, cost = units, 0.0
            for lot in lots.get(k, []):
                if left <= 0:
                    break
                if lot[0] > at:
                    continue
                take = min(lot[1], left)
                if take > 0:
                    cost += take * lot[2]
                    lot[1] -= take
                    left -= take
            out.append({"at": at, "item": item, "key": k, "units": units,
                        "proceeds": int(proceeds), "cost": int(round(cost)),
                        "uncosted": left})
        held = {k: [l for l in v if l[1] > 0] for k, v in lots.items()}
        return out, {k: v for k, v in held.items() if v}
    except Exception:
        return [], {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cost_of_goods_sold(all_runs: bool = False) -> "tuple[int, int, int, int]":
    try:
        sales, _held = costed_sales(all_runs=all_runs)
        cost = sum(s["cost"] for s in sales)
        priced = sum(s["units"] - s["uncosted"] for s in sales)
        unpriced = sum(s["uncosted"] for s in sales)
        spare = 0
        for s in sales:
            if s["uncosted"] and s["units"]:
                spare += s["proceeds"] * s["uncosted"] // s["units"]
        return cost, priced, unpriced, spare
    except Exception:
        return 0, 0, 0, 0


def sells_as(bought_name: str) -> str:
    if not bought_name:
        return ""
    bare = item_name(_PACK_ANYWHERE.sub(" ", bought_name)).strip()
    if not bare:
        return ""
    chaos_set = FAVOURITE_SLOTS.get(CHAOS_SET_SLOT, "")
    if chaos_set and _floor_key(bare) == _floor_key(item_name(chaos_set)):
        return chaos_set
    chaos_core = FAVOURITE_SLOTS.get(CHAOS_CORE_SLOT, "")
    if chaos_core and _floor_key(bare) == _floor_key(item_name(chaos_core)):
        return chaos_set or bare
    known = core_behind(bare)
    if known:
        return known

    dropped = re.sub(r"\bset\b\s*", "", bare, flags=re.IGNORECASE)
    dropped = re.sub(r"\s+", " ", dropped).strip()
    return dropped or bare


def bought_stock_report() -> str:
    if not PURCHASES and not SALES:
        return ""

    def key(name: str) -> str:
        return _floor_key(item_name(_PACK_ANYWHERE.sub(" ", name or "")))

    bought: dict = {}
    for row in PURCHASES:
        qty = int(row.get("qty") or 0)
        if qty <= 0:
            continue
        name = sells_as(row.get("item") or "")
        e = bought.setdefault(key(name), {"name": name, "qty": 0, "spend": 0})
        e["qty"] += qty
        e["spend"] += int(row.get("spend") or 0) or int(row.get("price") or 0)

    since = _RUN_STARTED_AT.isoformat(timespec="seconds")
    sold: dict = {}
    uncosted_rows: list = []
    for sale in costed_sales(run=since)[0]:
        e = sold.setdefault(sale["key"],
                            {"name": sale["item"], "units": 0, "gross": 0,
                             "cost": 0, "short": 0})
        e["units"] += sale["units"]
        e["gross"] += sale["proceeds"]
        e["cost"] += sale["cost"]
        e["short"] += sale["uncosted"]
        if sale["uncosted"]:
            spare = (sale["proceeds"] * sale["uncosted"] // sale["units"]
                     if sale["units"] else sale["proceeds"])
            uncosted_rows.append((sale["item"], sale["uncosted"], spare))

    started = _RUN_STARTED_AT.strftime("%Y-%m-%d %H:%M:%S")
    lines = ["", "=" * 89,
             "1. RESUPPLY THIS RUN -- bought, sold, and the profit on it",
             f"   run started {started} -- THIS RUN ONLY, not a running total",
             "=" * 89,
             f"  {'ITEM':<24}{'BOUGHT':>8}{'AVG BUY':>11}{'SOLD':>8}"
             f"{'TAKINGS':>16}{'PROFIT':>15}{'MARGIN':>7}"]

    t_bought = t_spend = t_sold = t_gross = t_cost = 0
    uncosted: list = uncosted_rows
    for k in sorted(set(bought) | set(sold),
                    key=lambda x: (bought.get(x) or sold[x])["name"]):
        b = bought.get(k)
        o = sold.get(k)
        name = (b or o)["name"]
        qty = b["qty"] if b else 0
        spend = b["spend"] if b else 0
        avg = -(-spend // qty) if qty else 0
        t_bought += qty
        t_spend += spend
        if o is None:
            lines.append(
                f"  {name[:23]:<24}{qty:>8,}{avg:>11,}{0:>8,}"
                f"{0:>16,}{'-':>15}{'-':>7}")
            continue
        if not qty and o["units"] == o["short"]:
            continue

        units = o["units"] - o["short"]
        gross = o["gross"] - sum(
            s for it, u, s in uncosted_rows if key(it) == k)
        cost = o["cost"]
        profit = gross - cost
        t_sold += units
        t_gross += gross
        t_cost += cost
        lines.append(
            f"  {name[:23]:<24}{qty:>8,}{avg:>11,}{units:>8,}"
            f"{gross:>16,}{profit:>+15,}{profit / gross:>7.1%}"
            if units and gross else
            f"  {name[:23]:<24}{qty:>8,}{avg:>11,}{units:>8,}"
            f"{gross:>16,}{'-':>15}{'-':>7}")

    t_profit = t_gross - t_cost
    lines += ["  " + "-" * 87,
              f"  {'TOTAL':<24}{t_bought:>8,}{'':>11}{t_sold:>8,}"
              f"{t_gross:>16,}{t_profit:>+15,}"
              + (f"{t_profit / t_gross:>7.1%}" if t_gross else f"{'-':>7}")]

    lines += ["", "=" * 89,
              "2. PROFIT THIS RUN -- resupply stock only",
              "=" * 89]
    if t_sold:
        lines += [
            f"  {'UNITS BOUGHT':<34}{t_bought:>20,}",
            f"  {'SPENT ON THEM':<34}{t_spend:>20,} Alz",
            f"  {'UNITS SOLD':<34}{t_sold:>20,}",
            "  " + "-" * 74,
            f"  {'GROSS TAKINGS':<34}{t_gross:>20,} Alz",
            f"  {'COST OF THOSE UNITS':<34}{t_cost:>20,} Alz",
            "  " + "-" * 74,
            f"  {'PROFIT':<34}{t_profit:>+20,} Alz",
        ]
        if t_gross:
            lines.append(f"    {t_profit / t_gross:.1%} margin on "
                         f"{t_sold:,} unit(s) sold this run")
    elif t_bought:
        lines.append(f"  Bought {t_bought:,} unit(s) for {t_spend:,} Alz and sold "
                     f"none of them yet, so this run has no profit to report.")
    else:
        lines.append("  Nothing bought and nothing sold this run.")

    if t_bought or t_sold:
        flow = t_gross + sum(g for _, _, g in uncosted) - t_spend
        lines += ["  " + "-" * 74,
                  f"  {'CASH FLOW THIS RUN':<34}{flow:>+20,} Alz",
                  "    every Alz in less every Alz out -- a restocking run ends "
                  "negative here and is not losing money"]

    if uncosted:
        lines += ["", "=" * 89,
                  "3. SOLD THIS RUN, BUT NOT BOUGHT THIS RUN",
                  "=" * 89,
                  f"  {'ITEM':<40}{'SOLD':>12}{'TAKINGS':>22}"]
        merged: dict = {}
        for name, units, gross in uncosted:
            e = merged.setdefault(key(name), {"name": name, "u": 0, "g": 0})
            e["u"] += units
            e["g"] += gross
        u_units = u_gross = 0
        for e in sorted(merged.values(), key=lambda x: x["name"]):
            lines.append(f"  {e['name'][:39]:<40}{e['u']:>12,}{e['g']:>18,} Alz")
            u_units += e["u"]
            u_gross += e["g"]
        lines += ["  " + "-" * 74,
                  f"  {'TOTAL':<40}{u_units:>12,}{u_gross:>18,} Alz",
                  "    stock from an earlier run or from before the ledger, so "
                  "this run paid nothing for it",
                  "    and no profit is claimed on it here"]

    lines.append("")
    return "\n".join(lines)


def profit_report() -> str:
    gross = sum(s["proceeds"] or 0 for s in SALES)
    spend = sum(p["spend"] or 0 for p in PURCHASES)
    totals = all_time_totals()

    if not SALES and not PURCHASES and not totals:
        return ""

    net = gross - spend
    if SALES or PURCHASES:
        lines = ["", "=" * 74,
                 f"THIS RUN: {len(SALES)} collection(s) in {gross:,} Alz, "
                 f"{len(PURCHASES)} purchase(s) out {spend:,} Alz",
                 f"          net {net:+,} Alz",
                 "=" * 74]
    else:
        lines = ["", "=" * 74,
                 "THIS RUN: nothing collected and nothing bought.",
                 "=" * 74]

    if totals:
        sales_n, all_gross, buys_n, all_spend, all_fees = totals
        cogs, priced, unpriced, _uncosted = cost_of_goods_sold(all_runs=True)
        held = all_spend - cogs
        lines += [
            f"  STANDING POSITION -- ALL RUNS (stock and cash, not profit)",
            f"  {sales_n} collection(s)      in  {all_gross:>18,} Alz",
            f"  {buys_n} purchase(s)        out {all_spend:>18,} Alz",
        ] + ([
            f"  registration fees       {all_fees:>18,} Alz",
        ] if all_fees else []) + [
            "  " + "-" * 70,
        ]
        if held < 0:
            lines += [
                f"  {'INVENTORY (at cost)':22} {'not computable':>29}",
                f"    more units have sold ({priced + unpriced:,}) than the "
                f"purchases cover, by {-held:,} Alz of cost - so stock cannot "
                f"be valued from this ledger alone",
            ]
        else:
            lines += [
                f"  {'INVENTORY (at cost)':22} {held:>29,} Alz",
                "    paid for and not yet sold - stock, not a loss",
            ]
        lines += [
            "  " + "-" * 70,
            f"  {'CASH FLOW':22} {all_gross - all_spend:>+29,} Alz",
            "    every Alz in less every Alz out, including stock still held",
        ]
        if len(SALES) and not len(PURCHASES):
            lines.append("  (this run bought nothing, so its own net is just "
                         "the takings)")
    lines.append("")
    return "\n".join(lines)


def sale_alert(item: str, qty: "int | None", unit: "int | None",
               proceeds: int) -> str:
    try:
        bits = [f"+{proceeds:,} Alz", f"{item} x{qty:,}" if qty else (item or "?")]
        sales, _held = costed_sales()
        mine = sales[-1] if sales else None
        if mine and mine["proceeds"] == proceeds and not mine["uncosted"]:
            made = proceeds - mine["cost"]
            each = mine["cost"] // max(1, mine["units"])
            if unit:
                bits.append(f"sold {unit:,} / cost {each:,}")
            bits.append(f"made {made:+,} ({made / proceeds:.0%})")
        elif mine and mine["uncosted"]:
            bits.append(f"{mine['uncosted']:,} unit(s) predate the ledger, "
                        "so no cost is known")
        elif unit:
            bits.append(f"sold {unit:,} ea")
        return "  |  ".join(bits)
    except Exception:
        return f"+{proceeds:,} Alz  {item or '?'}"


def sales_report() -> str:
    if not SALES:
        return ""

    by_item: dict[str, dict] = {}
    for sale in SALES:
        entry = by_item.setdefault(sale["item"],
                                   {"n": 0, "qty": 0, "gross": 0,
                                    "unmeasured": 0})
        entry["n"] += 1
        if sale["proceeds"]:
            entry["gross"] += sale["proceeds"]
        else:
            entry["unmeasured"] += 1
        if sale["qty"]:
            entry["qty"] += sale["qty"]

    gross = sum(e["gross"] for e in by_item.values())
    unmeasured = sum(e["unmeasured"] for e in by_item.values())

    lines = ["", "=" * 74,
             f"SOLD THIS RUN: {len(SALES)} collection(s), "
             f"{gross:,} Alz gross", "=" * 74,
             f"  {'item':42} {'sales':>5} {'qty':>7} {'gross':>14}",
             "  " + "-" * 70]
    for name, e in sorted(by_item.items(), key=lambda kv: -kv[1]["gross"]):
        shown = name if len(name) <= 42 else name[:39] + "..."
        qty = f"{e['qty']:,}" if e["qty"] else "-"
        lines.append(f"  {shown:42} {e['n']:>5} {qty:>7} {e['gross']:>14,}")
    lines.append("  " + "-" * 70)
    lines.append(f"  {'TOTAL':42} {len(SALES):>5} "
                 f"{sum(e['qty'] for e in by_item.values()):>7,} "
                 f"{gross:>14,}")
    if unmeasured:
        lines.append(f"\n  {unmeasured} sale(s) could not be measured (the Alz "
                     f"balance was unreadable), so the gross above is a floor, "
                     f"not the whole of it.")
    return "\n".join(lines)


def recover_stranded_work_tab(timeout: float = 8.0,
                              verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    outstanding = carried_total()
    if outstanding:
        say(f"  the work tab holds {outstanding} Set(s) a restock has already "
            "paid for and still owes a listing -- leaving them for the "
            "restock to convert rather than listing them as a strand.")
        return False

    origin = inventory_origin()
    if origin is None:
        say("  the Inventory panel is not visible, so the strand cannot be "
            "cleared automatically.")
        return False
    if not select_inventory_tab(WORK_TAB, origin):
        say(f"  could not switch to inventory tab {WORK_TAB}.")
        return False

    price = strictest_price_floor() or FALLBACK_PRICE
    say(f"  re-listing the stranded stack at {price:,} Alz (the strictest "
        "floor on the books) - it cannot be named from an inventory slot, so "
        "its own floor cannot be looked up. The next cycle reads the name off "
        "the table and re-prices it properly.")

    for attempt in range(1, STRAND_RECOVERY_ATTEMPTS + 1):
        park_cursor()
        occupied = occupied_slots(grab(), origin)
        if not occupied:
            say(f"  inventory tab {WORK_TAB} is clear.")
            return True
        row, col = occupied[0]
        say(f"  attempt {attempt}/{STRAND_RECOVERY_ATTEMPTS}: "
            f"{len(occupied)} slot(s) in use; listing slot ({row},{col}).")
        record("strand.recovering", tab=WORK_TAB, occupied=len(occupied),
               slot=f"{row},{col}", price=price, attempt=attempt)
        try:
            listed = register_item(row, col, timeout=timeout, verbose=verbose,
                                   force_price=price, maximise_qty=True)
        except (Aborted, PermissionError) as exc:
            say(f"  could not list it: {exc}")
            return False
        if listed:
            note_rows_added(1)
        if not listed:
            say("  the re-listing did not complete.")
            return False
        if not select_inventory_tab(WORK_TAB, origin):
            return False

    park_cursor()
    still = occupied_slots(grab(), origin)
    if still:
        say(f"  {len(still)} slot(s) still in use after "
            f"{STRAND_RECOVERY_ATTEMPTS} attempts - stopping rather than "
            "retrying for ever.")
        return False
    return True


def ensure_work_tab_empty(timeout: float = 8.0, verbose: bool = True) -> bool:
    if require_empty_work_tab(verbose=verbose):
        return True

    if verbose:
        why = ("this run's own working stock" if carried_total() > 0
               or chaos_stranded() else
               "stock this script cannot name from a slot")
        print(f"  inventory tab {WORK_TAB} is not empty ({why}); skipping "
              f"this cycle. Nothing has been listed or cancelled.")
    return False


def changed_slots(
    before: Image.Image,
    after: Image.Image,
    origin: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    if origin is None:
        origin = inventory_origin(before) or inventory_origin(after) or inventory_origin()
    if origin is None:
        return []

    old, new = inventory_cells(before, origin), inventory_cells(after, origin)
    changed: list[tuple[int, int]] = []
    for key, cell in old.items():
        diff = ImageChops.difference(cell, new[key])
        flat = getattr(diff, "get_flattened_data", diff.getdata)()
        delta = sum(flat) / (diff.width * diff.height)
        if delta >= SLOT_CHANGE_MIN:
            changed.append(key)
    return sorted(changed)


def changed_slot(
    before: Image.Image,
    after: Image.Image,
    origin: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    slots = changed_slots(before, after, origin)
    return slots[0] if slots else None


def tab_centre(origin: tuple[int, int], tab: int) -> tuple[int, int]:
    if not 1 <= tab <= TAB_COUNT:
        raise ValueError(f"tab {tab} is outside I..{TAB_COUNT}")
    return (round(origin[0] + TAB_ONE_OFFSET[0] + TAB_PITCH * (tab - 1)),
            round(origin[1] + TAB_ONE_OFFSET[1]))


def active_inventory_tab(
    source: Image.Image | None = None, origin: tuple[int, int] | None = None
) -> int | None:
    image = source if source is not None else grab()
    if origin is None:
        origin = inventory_origin(image)
    if origin is None:
        return None

    brightness = []
    for tab in range(1, TAB_COUNT + 1):
        cx, cy = tab_centre(origin, tab)
        box = (max(0, int(cx - TAB_SAMPLE_HALF_W)),
               max(0, int(cy + TAB_SAMPLE_BAND[0])),
               min(image.width, int(cx + TAB_SAMPLE_HALF_W)),
               min(image.height, int(cy + TAB_SAMPLE_BAND[1])))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        cell = image.crop(box).convert("L")
        data = list(getattr(cell, "get_flattened_data", cell.getdata)())
        brightness.append((sum(data) / len(data), tab))

    values = sorted(v for v, _ in brightness)
    median = (values[TAB_COUNT // 2 - 1] + values[TAB_COUNT // 2]) / 2
    best = max(brightness)
    return best[1] if best[0] - median >= TAB_ACTIVE_MARGIN else None


def select_inventory_tab(
    tab: int, origin: tuple[int, int] | None = None, timeout: float = 5.0
) -> bool:
    if origin is None:
        origin = inventory_origin()
    if origin is None:
        return False
    if active_inventory_tab(origin=origin) == tab:
        return True

    click(*tab_centre(origin, tab))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if active_inventory_tab(origin=origin) == tab:
            return True
        time.sleep(0.4)
    return False


def _digits(text: str) -> int | None:
    cleaned = re.sub(r"[^0-9]", "", text)
    return int(cleaned) if cleaned else None


PRICE_TEXT_MAX_X = 230
PRICE_MIN_CONF = 15.0


def _price_value(text: str) -> int | None:
    cleaned = re.sub(r"a[il1]z", "", text, flags=re.IGNORECASE)
    digits = re.sub(r"[^0-9]", "", cleaned)
    if digits:
        return int(digits)
    if re.fullmatch(r"[Oo0°©()\s.,]*", cleaned) and \
            re.search(r"[Oo0°©()]", cleaned):
        return 0
    return None


def shop_slot_stdev(image: Image.Image) -> float:
    return ImageStat.Stat(image.crop(SHOP_SLOT_BOX).convert("L")).stddev[0]


def shop_slot_free(image: "Image.Image | None" = None) -> "bool | None":
    shot = image if image is not None else grab()
    return None if shop_slot_stdev(shot) >= SHOP_SLOT_STDEV else True


def _panel_slice(words: list, box: tuple, min_conf: float,
                 image, fallback: bool = True) -> list:
    inside = [w for w in words
              if box[0] <= w.centre[0] <= box[2]
              and box[1] <= w.centre[1] <= box[3]
              and w.conf >= min_conf]
    if inside or not fallback:
        return inside
    return find_words(image, box, min_conf)


def read_register_panel(source: Image.Image | Path | str) -> dict:
    image = source if isinstance(source, Image.Image) else Image.open(source)

    panel_box = (min(PRICE_ROWS[0], PRICE_FIELD[0], QTY_FIELD[0],
                     NET_SALES_ROWS[0]),
                 min(PRICE_ROWS[1], PRICE_FIELD[1], QTY_FIELD[1],
                     NET_SALES_ROWS[1]),
                 max(PRICE_ROWS[2], PRICE_FIELD[2], QTY_FIELD[2],
                     NET_SALES_ROWS[2]),
                 max(PRICE_ROWS[3], PRICE_FIELD[3], QTY_FIELD[3],
                     NET_SALES_ROWS[3]))
    panel_words = find_words(image, panel_box, 0.0)

    all_words = _panel_slice(panel_words, PRICE_ROWS, 0.0, image)
    words = [w for w in all_words if w.conf >= PRICE_MIN_CONF]
    lenient: list | None = None
    prices: list[tuple[int, int]] = []
    for expected_y in (PRICE_TOP_Y, PRICE_BOTTOM_Y):
        on_row = [w for w in words
                  if abs(w.centre[1] - expected_y) <= PRICE_ROW_Y_TOL
                  and w.centre[0] < PRICE_TEXT_MAX_X]
        if not on_row:
            if lenient is None:
                lenient = all_words
            on_row = [w for w in lenient
                      if abs(w.centre[1] - expected_y) <= PRICE_ROW_Y_TOL
                      and w.centre[0] < PRICE_TEXT_MAX_X]
        if not on_row:
            continue
        text = "".join(w.text for w in sorted(on_row, key=lambda w: w.left))
        value = _price_value(text)
        if value is None:
            continue
        y = round(sum(w.centre[1] for w in on_row) / len(on_row))
        prices.append((value, y))

    typed = None
    for word in _panel_slice(panel_words, PRICE_FIELD, 40.0, image):
        typed = _digits(word.text) or typed

    qty = qty_max = None
    qty_text = " ".join(
        w.text for w in sorted(
            _panel_slice(panel_words, QTY_FIELD, QTY_MIN_CONF, image),
            key=lambda w: w.left)
    )
    numbers = [_digits(chunk) for chunk in re.findall(r"\d[\d,]*", qty_text)]
    numbers = [n for n in numbers if n is not None]
    if numbers:
        qty = numbers[0]
        if len(numbers) > 1:
            qty_max = numbers[-1]

    net_cell = sorted(_panel_slice(panel_words, NET_SALES_ROWS, 40.0, image),
                      key=lambda w: w.left)
    net = _digits("".join(w.text for w in net_cell)) or 0

    stdev = shop_slot_stdev(image)
    loaded = stdev >= SHOP_SLOT_STDEV or bool(qty) or net > 0

    return {"prices": [p for p, _ in prices], "price_rows": prices, "typed": typed,
            "qty": qty, "qty_max": qty_max, "qty_text": qty_text,
            "net_sales": net, "loaded": loaded, "slot_stdev": round(stdev, 1)}


def dialog_kind_by_buttons(shot) -> "str | None":
    if dialog_button_band(RECEIPT_WORD, source=shot) is not None:
        return "receipt"
    has_confirm = dialog_button_band(CONFIRM_WORD, source=shot) is not None
    has_register = dialog_button_band(REGISTER_TAB_WORD, source=shot) is not None
    if has_confirm and not has_register:
        return "confirm"
    if has_register and not has_confirm:
        return "extension"
    return None


def await_dialog(kind: str | None, timeout: float = 8.0, poll: float = 0.35):
    if kind is not None:
        park_cursor()
    deadline = time.monotonic() + timeout
    while True:
        shot = grab()
        if dialog_kind_by_buttons(shot) == kind or dialog_kind(shot) == kind:
            return shot
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


class Aborted(Exception):
    pass


class ShopEmpty(Exception):
    pass


class ShopIdle(Exception):
    pass


def cancel_item(
    row: int,
    absolute_row: "int | None" = None,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    expect: "RowRef | None" = None,
    report: "dict | None" = None,
) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    if report is not None:
        report.setdefault("committed", False)
        report.setdefault("reason", "")

    committed = False
    try:
        if not dry_run:
            require(not session_locked(),
                    "the workstation is locked - screen capture is blank and "
                    "input goes to the secure desktop")
            require(focus_game(), "could not bring Cabal to the foreground")
            park_cursor()
            require(
                not table_loading(grab()) or wait_for_table(max(timeout, 20.0)),
                "the table is still waiting for the server response",
            )
            require(
                dialog_kind(grab()) is None,
                "a dialog was already open before starting",
            )

        rows = await_rows(timeout)
        require(bool(rows),
                "no listings visible - is the Trade window open on Register?")
        require(1 <= row <= len(rows),
                f"row {row} is out of range; {len(rows)} row(s) visible")

        target = rows[row - 1]
        say(f"Row {row}: {target.name!r} [{target.action}] -> button at {target.change}")
        require(target.cancellable,
                f"row {row} shows '{target.action}', not 'Change'")

        if expect is not None:
            resolved, note = locate_row(rows, expect)
            moved = f"it is now at row {resolved.index}" if resolved else "it is gone"
            require(resolved is not None and resolved.index == row,
                    f"row {row} no longer holds {expect.name!r} "
                    f"({note or moved}) - the table changed since it was "
                    "chosen, so nothing was cancelled")
            if note:
                say(f"  {note}")
            say(f"  identity confirmed: row {row} still holds {expect.name!r}")
            SHOP.check(absolute_row or row, resolved)

        if dry_run:
            say("[dry run] would click Change -> Cancel -> Confirmation")
            return True

        record("cancel.before_change", row=row, name=target.name,
               price=target.price, qty=target.qty)
        click(*target.change)
        park_cursor()
        shot = await_dialog("extension", timeout)
        record("cancel.after_change", shot, row=row, name=target.name,
               dialog="extension" if shot else "none")
        if shot is None:
            probe = grab()
            say(f"  dialog_kind sees: {dialog_kind(probe)!r}")
            say(f"  trade window still open: {trade_window_open(probe)}")
            words = sorted(find_words(probe, POPUP_REGION, 25),
                           key=lambda w: -w.conf)[:12]
            say("  strongest words in the dialog area: "
                + ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words))
            if dialog_kind(probe) == "extension":
                say("  ...but it IS up on a fresh frame: it arrived after the "
                    "wait expired. Continuing rather than aborting.")
                shot = probe
            else:
                shot = await_dialog("extension", EXTENSION_RECHECK_SECONDS)
                if shot is not None:
                    say("  ...it IS up on a fresh frame after a second look; "
                        "continuing rather than aborting.")
            if shot is None:
                probe = grab()
                by_register = dialog_button_band(REGISTER_TAB_WORD, source=probe)
                by_confirm = dialog_button_band(CONFIRM_WORD, source=probe)
                if by_register is not None and by_confirm is None:
                    say(f"  ...its title would not read, but the button row "
                        f"carries {REGISTER_TAB_WORD} at {by_register.centre} "
                        f"(conf {by_register.conf:.0f}) and no "
                        f"{CONFIRM_WORD}, which is this dialog and no other. "
                        f"Continuing on the buttons rather than aborting.")
                    record("cancel.extension_by_button",
                           at=str(by_register.centre))
                    shot = probe
        require(shot is not None, "the Registration Extension dialog did not appear")

        cancel = await_dialog_button(DISMISS_WORD, timeout, source=shot)
        require(cancel is not None,
                "no Cancel button on the Registration Extension dialog")

        say(f"{DISMISS_WORD} button at {cancel.centre} (conf {cancel.conf:.0f})")
        click(*cancel.centre)
        shot = await_dialog("confirm", timeout)
        require(shot is not None, "the confirmation dialog did not appear")

        confirm = await_dialog_button(CONFIRM_WORD, timeout, source=shot)
        require(confirm is not None,
                "no Confirmation button on the confirmation dialog")

        say(f"{CONFIRM_WORD} button at {confirm.centre} (conf {confirm.conf:.0f})")
        if report is not None:
            report["committed"] = True
        forget_range_view()
        click(*confirm.centre)
        committed = True
        gone = await_dialog(None, timeout) is not None
        reclicks = 0
        while not gone and reclicks < CONFIRM_RECLICKS:
            again = dialog_button_band(CONFIRM_WORD)
            if again is None:
                break
            reclicks += 1
            say(f"  the dialog is still open and {CONFIRM_WORD} is still on "
                f"screen at {again.centre}; pressing it again "
                f"({reclicks} of {CONFIRM_RECLICKS}).")
            record("cancel.reconfirm", attempt=reclicks, row=row)
            click(*again.centre)
            park_cursor()
            gone = await_dialog(None, timeout) is not None
        require(gone, "the dialog stayed open after Confirmation")

        record("cancel.committed", row=row, name=target.name,
               price=target.price, qty=target.qty)
        SHOP.cancel(absolute_row or row)
        say(f"Cancelled registration on row {row}: {target.name!r}.")
        return True

    except Aborted as exc:
        still = dialog_kind(grab()) if committed else None
        if report is not None:
            report["committed"] = committed
            report["reason"] = str(exc)
        record("cancel.aborted", reason=str(exc), row=row, committed=committed,
               dialog_after=still,
               accepted=None if not committed else (still != "confirm"))
        say(f"ABORTED: {exc}.")
        if committed:
            if still == "confirm":
                say("A confirmation dialog is still open. USUALLY that "
                    "means the game refused the cancellation and the "
                    "listing is untouched - but the game can also stack "
                    "dialogs after accepting one, so this is not proof.")
                say("CHECK THE LISTING before retrying: if it is gone, the "
                    f"stack is in inventory tab {WORK_TAB}, unlisted.")
                say("A likely cause is not enough free inventory space to "
                    "receive the stack: a cancelled 250-item listing comes "
                    "back as ~64 separate slots, and the game refuses rather "
                    "than partially withdrawing. Note the check covers your "
                    "WHOLE inventory, not just the work tab -- this has been "
                    "observed refusing while the work tab was empty.")
                say("Retrying this row will refuse identically until space is "
                    "freed, so the run will stop after "
                    f"{MAX_CONSECUTIVE_FAILURES} attempts.")
            else:
                say("WARNING: Confirmation was already clicked and the dialog "
                    f"is now {still!r}, so the cancellation may have gone "
                    "through. Check the listing before retrying.")
            return False
        if dry_run:
            say("[dry run] leaving the screen exactly as it is.")
            return False
        if not dialog_present():
            say("Nothing was changed.")
        elif close_any_dialog():
            say("Backed out of the open dialog; nothing was changed.")
        else:
            say("WARNING: could not close the dialog still on screen - "
                "dismiss it manually before rerunning.")
        return False


def clear_shop_slot(timeout: float = 15.0, verbose: bool = True) -> bool:
    if not trade_window_open():
        if verbose:
            print("The Trade window is not open, so the shop slot cannot be read.")
        return False

    panel = read_register_panel(grab())
    if not panel["loaded"]:
        return True
    if verbose:
        print(f"Returning the shop slot item (qty {panel['qty_text']!r}) to the inventory")
    ctrl_click(*SHOP_SLOT)
    park_cursor()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if shop_slot_free() is not None and not read_register_panel(grab())["loaded"]:
            return True
        time.sleep(0.5)
    return False


def dialog_shows_amount(shot, amount: int) -> bool:
    want = f"{int(amount)}"
    try:
        words = find_words(shot, POPUP_REGION, DIALOG_TEXT_MIN_CONF)
    except Exception:
        return False
    seen = "".join(re.sub(r"[^0-9]", "", w.text or "") for w in words)
    return want in seen


def verify_undercut(price: int, undercut: int, suggested: "int | None",
                    applied: bool, say, require) -> None:
    if not applied:
        say(f"  the undercut did not apply: a floor held the price at "
            f"{price:,} Alz. Listing at the floor is the correct outcome.")
        return
    if suggested and price <= suggested:
        say(f"  undercut: listing at {price:,}, which is "
            f"{suggested - price:,} below the {suggested:,} market lowest "
            f"that was read.")
    elif suggested:
        say(f"  listing at {price:,}, which is {price - suggested:,} ABOVE "
            f"the {suggested:,} market lowest - a floor or the ratchet held "
            f"it there, which is the protection working.")
    else:
        say(f"  undercut applied: listing at {price:,} (no market price was "
            f"readable to compare against).")


def register_item(
    row: int,
    col: int,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    price_floor: int = 0,
    floor_price: int | None = None,
    floor_reason: str = "",
    cost_floor: int = 0,
    maximise_qty: bool | None = None,
    force_price: int | None = None,
    force_qty: int | None = None,
    expect_item: str | None = None,
    undercut: int = 0,
    expect_qty: int | None = None,
    report: dict | None = None,
) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise Aborted(reason)

    committed = False
    try:
        if not dry_run:
            require(focus_game(), "could not bring Cabal to the foreground")
            park_cursor()
            require(not table_loading(grab()) or wait_for_table(max(timeout, 20.0)),
                    "the table is still waiting for the server response")
            require(dialog_kind(grab()) is None, "a dialog was already open")

        if not dry_run:
            require(trade_window_open(),
                    "the Trade window is not open, so there is no shop slot to "
                    "load into - open the Agent Shop first")

        panel = read_register_panel(grab())
        require(not panel["loaded"],
                f"the shop slot already holds an item "
                f"(qty {panel['qty_text']!r}, spread {panel['slot_stdev']})")

        centre = slot_centre(row, col)
        record("register.before_load", row=row, col=col, item=expect_item)
        say(f"Ctrl+Click inventory slot ({row},{col}) at {centre}")
        if dry_run:
            say("[dry run] would load the slot, set the price, then Register")
            return True

        panel = {"loaded": False}
        for attempt in range(1, LOAD_ATTEMPTS + 1):
            ctrl_click(*centre)
            time.sleep(0.8)
            park_cursor()
            panel = read_register_panel(grab())
            if panel["loaded"]:
                break
            if attempt < LOAD_ATTEMPTS:
                say(f"Slot ({row},{col}) did not load on attempt {attempt}; retrying.")
                time.sleep(0.6)
        require(panel["loaded"],
                f"nothing loaded into the shop slot after {LOAD_ATTEMPTS} attempts "
                f"(slot ({row},{col}) may be empty, or the item moved)")
        say(f"Loaded: qty {panel['qty_text']!r} -> {panel['qty']}/{panel['qty_max']}, "
            f"suggested {panel['prices'] or 'none'}")

        if (expect_item and expect_qty is not None
                and panel["qty_max"] is None):
            time.sleep(0.4)
            again = read_register_panel(grab())
            if again.get("loaded") and again.get("qty_max") is not None:
                say(f"  the quantity field re-read as {again['qty_max']} "
                    "(it was unreadable a moment ago).")
                panel = again

        if (expect_item and expect_qty is not None
                and panel["qty_max"] is None):
            say(f"WARNING: the panel's quantity field did not read, so the "
                f"cross-check against the cancelled listing's {expect_qty} "
                f"could NOT be performed. The listing will still be verified "
                f"against the table afterwards.")
            record("register.qty_unverified", item=expect_item,
                   expect_qty=expect_qty, row=row, col=col,
                   qty_text=panel.get("qty_text"))
            if report is not None:
                report["qty_unverified"] = True

        if expect_item and expect_qty is not None and panel["qty_max"] is not None:
            loaded = panel["qty_max"]
            slack = max(QTY_CROSSCHECK_ABSOLUTE,
                        int(expect_qty * QTY_CROSSCHECK_FRACTION))

            if loaded >= expect_qty:
                if loaded > expect_qty:
                    say(f"NOTE: the panel offers {loaded} but the cancelled "
                        f"listing held {expect_qty}. The extra are the same "
                        "item held elsewhere in the inventory, which the shop "
                        "slot gathers; continuing.")
                    if report is not None:
                        report["qty_extra"] = (expect_qty, loaded)
            elif expect_qty - loaded <= slack:
                say(f"NOTE: the panel holds {loaded} but the table said "
                    f"{expect_qty} (short by {expect_qty - loaded}, within "
                    f"{slack}). The panel field is the more reliable read, so "
                    "continuing with it - the table's QTY column is narrow and "
                    "misreads a digit occasionally.")
                if report is not None:
                    report["qty_disagreement"] = (expect_qty, loaded)
            else:
                require(False,
                        f"the shop slot offers only {loaded} but the cancelled "
                        f"listing held {expect_qty} - short by "
                        f"{expect_qty - loaded}, more than the {slack} "
                        f"tolerated. The cancelled stack should be in the "
                        f"inventory, so either it is not all there or this is "
                        f"the wrong slot")

        if maximise_qty is None:
            if force_qty:
                maximise_qty = False
            elif expect_item:
                maximise_qty = wants_max_quantity(expect_item)
            else:
                require(not (MAXIMISE_ALL_QUANTITIES and NO_MAX_QUANTITY_ITEMS),
                        "cannot decide whether to maximise the quantity: the "
                        "item in this slot cannot be named, so "
                        f"NO_MAX_QUANTITY_ITEMS {NO_MAX_QUANTITY_ITEMS} cannot "
                        "be checked against it. Pass --qty N or --max-qty to "
                        "say which you want")
                maximise_qty = MAXIMISE_ALL_QUANTITIES

        entry = force_qty if force_qty else (MAX_QTY_ENTRY if maximise_qty else None)

        if entry is not None and not force_qty and panel.get("qty_max") == 1:
            say("  the stack holds one item, so there is no quantity to "
                "maximise - leaving the field alone.")
            record("qty.single_stack", item=expect_item)
            entry = None

        if entry is not None:
            record("qty.before_typing", entry=entry, item=expect_item)
            say(f"Setting quantity: typing {entry}"
                + ("" if force_qty else " - the game clamps it to the stack maximum"))
            click(*QTY_INPUT)
            type_number(entry, clear=len(str(MAX_QTY_ENTRY)) + 2)
            time.sleep(0.4)
            park_cursor()

        rows_seen = panel["price_rows"]

        if expect_item:
            absolute_floor, floor_reason_text = listing_floor(expect_item)

            absolute_floor, floor_reason_text = effective_floor(
                absolute_floor, floor_reason_text, cost_floor)
            if absolute_floor:
                say(f"Floor for this item: {absolute_floor:,} Alz "
                    f"({floor_reason_text})")
        else:
            absolute_floor = 0
            require(force_price is not None,
                    "cannot price an item the script cannot name, because its "
                    "price floor cannot be looked up. Use --relist, which "
                    "reads the name off the listing, or pass --price to state "
                    "the price yourself")
            strictest = strictest_price_floor()
            require(not strictest or force_price >= strictest,
                    f"--price {force_price:,} is below the strictest floor on "
                    f"the books ({strictest:,}) and the item cannot be named "
                    f"here, so it might be one the floor protects. Use "
                    f"--relist, which reads the name off the listing")
        if absolute_floor:
            say(f"Absolute floor for this item: {absolute_floor:,} Alz")

        if force_price is not None:
            require(not (expect_item and absolute_floor
                         and force_price < absolute_floor),
                    f"--price {force_price:,} is below the {absolute_floor:,} "
                    f"floor for {expect_item!r}")
            suggested, price_y = force_price, None
            price, why = force_price, "forced by --price"
            say(f"Price forced to {force_price:,} Alz")
        else:
            require(bool(rows_seen), "no suggested-price rows could be read")

            suggested, price_y = min(rows_seen,
                                     key=lambda r: abs(r[1] - PRICE_BOTTOM_Y))
            require(abs(price_y - PRICE_BOTTOM_Y) <= PRICE_ROW_Y_TOL,
                    f"the lowest-current-price row was not found; read {rows_seen} "
                    f"(expected a row near y={PRICE_BOTTOM_Y})")

            average = next((p for p, y in rows_seen
                            if abs(y - PRICE_TOP_Y) <= PRICE_ROW_Y_TOL), None)
            record("price.suggestions", lowest=suggested, rows=str(rows_seen))
            say(f"Suggested: lowest current {suggested:,}, week average "
                + (f"{average:,}" if average else "unread"))
            if price_floor:
                say(f"--floor: {price_floor:,} Alz"
                    + (f" ({floor_reason})" if floor_reason else ""))

            if (average and suggested > 0
                    and suggested < average * SUSPECT_PRICE_FRACTION):
                guarded = -(-average * int(SUSPECT_PRICE_FRACTION * 100) // 100)
                say(f"NOTE: lowest current {suggested:,} is only "
                    f"{suggested / average:.1%} of the {average:,} week "
                    f"average - treating that as a misread and flooring at "
                    f"{guarded:,}.")
                record("price.below_week_average", suggested=suggested,
                       average=average, floored_to=guarded,
                       item=expect_item or "")
                absolute_floor = max(absolute_floor, guarded)
                if not floor_reason:
                    floor_reason = "half the week average"

            price, why = choose_price(suggested, price_floor, floor_price,
                                      absolute_floor)
            if why and floor_reason:
                why = f"{why} ({floor_reason})"

            if (floor_price and suggested > 0
                    and suggested < floor_price * SUSPECT_PRICE_FRACTION):
                say(f"NOTE: market {suggested:,} is only "
                    f"{suggested / floor_price:.1%} of the previous "
                    f"{floor_price:,} - a drop that large is as likely to be a "
                    f"misread as a real market, so it is listed at "
                    f"{price:,} instead.")

        require(price >= MIN_PLAUSIBLE_PRICE,
                f"refusing to list at {price:,} Alz, below the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price was "
                "probably misread")
        undercut_applied = False
        if undercut and price > 0:
            floor_now = max(absolute_floor or 0, MIN_PLAUSIBLE_PRICE)
            if not absolute_floor and floor_price:
                floor_now = max(floor_now, floor_price)
            lowered = max(price - undercut, floor_now)
            if lowered != price:
                say(f"  undercutting {price:,} by {price - lowered:,} to "
                    f"{lowered:,} Alz")
                price = lowered
                undercut_applied = True
                why = ", ".join(x for x in (why, f"undercut by {undercut:,}")
                                if x)
            else:
                say(f"  not undercutting: {price:,} is already at the floor")

        require(price >= MIN_PLAUSIBLE_PRICE,
                f"refusing to list at {price:,} Alz after the undercut, below "
                f"the {MIN_PLAUSIBLE_PRICE:,} plausibility floor")
        require(not absolute_floor or price >= absolute_floor,
                f"refusing to list at {price:,} Alz, below the "
                f"{absolute_floor:,} floor for this item")

        if undercut:
            say(f"Listing at {price:,} Alz - {why}")
            click((PRICE_FIELD[0] + PRICE_FIELD[2]) // 2,
                  (PRICE_FIELD[1] + PRICE_FIELD[3]) // 2)
            type_number(price)
        elif price == suggested and price_y is not None and price > 0:
            record("price.before_select", price=price, y=price_y)
            click(PANEL_RADIO_X, price_y)
        else:
            say(f"Overriding to {price:,} Alz - {why}")
            click((PRICE_FIELD[0] + PRICE_FIELD[2]) // 2,
                  (PRICE_FIELD[1] + PRICE_FIELD[3]) // 2)
            type_number(price)
        time.sleep(0.5)

        park_cursor()
        panel = read_register_panel(grab())
        require(panel["net_sales"] > 0,
                f"price did not take - net sales is still {panel['net_sales']}")

        for _ in range(QTY_READBACK_TRIES):
            if panel["net_sales"] % price == 0:
                break
            time.sleep(QTY_READBACK_PAUSE)
            panel = read_register_panel(grab())
        require(panel["net_sales"] % price == 0,
                f"the price on screen is not {price:,}: net sales "
                f"{panel['net_sales']:,} does not divide by it, so the field "
                f"holds something else. Nothing has been registered")
        say(f"  price verified on screen: {price:,} Alz "
            f"(net sales {panel['net_sales']:,})")

        require(panel["net_sales"] % price == 0,
                f"net sales {panel['net_sales']:,} is not a whole multiple of the "
                f"{price:,} price that was set - the price did not take correctly")
        qty = panel["net_sales"] // price
        say(f"Net sales {panel['net_sales']:,} Alz = {price:,} x {qty}"
            f"  (field reads {panel['qty_text']!r})")

        if undercut:
            verify_undercut(price=price, undercut=undercut,
                            suggested=suggested, applied=undercut_applied,
                            say=say, require=require)

        shot = grab()
        buttons = find_text(shot, "Register", REGISTER_PANEL)
        require(bool(buttons), "could not find the Register button")
        want_y = LAYOUT.y(984) if layout_is_fitted() else None
        button = (min(buttons, key=lambda w: abs(w.centre[1] - want_y))
                  if want_y is not None else buttons[-1])
        record("register.priced", shot, price=price, qty=panel.get("qty"),
               net_sales=panel.get("net_sales"), item=expect_item)
        say(f"Register button at {button.centre} (conf {button.conf:.0f})")

        click(*button.centre)
        shot = await_dialog("confirm", timeout)
        if shot is None:
            probe = grab()
            say(f"  dialog_kind sees: {dialog_kind(probe)!r}")
            say(f"  any dialog present: {dialog_present(probe)}")
            words = sorted(find_words(probe, POPUP_REGION, DIALOG_TEXT_MIN_CONF),
                           key=lambda w: -w.conf)[:12]
            say("  strongest words in the dialog area: "
                + ", ".join(f"{w.text!r}@{w.conf:.0f}" for w in words))
            record("register.no_confirm", probe, price=price,
                   qty=panel.get("qty"), item=expect_item)
            if dialog_kind(probe) == "confirm":
                say("  ...but it IS up on a fresh frame: it arrived after the "
                    "wait expired. Continuing rather than aborting.")
                shot = probe
            else:
                shot = await_dialog("confirm", EXTENSION_RECHECK_SECONDS)
                if shot is not None:
                    say("  ...it IS up on a fresh frame after a second look; "
                        "continuing rather than aborting.")
            if shot is None:
                probe = grab()
                accept = dialog_button_band(CONFIRM_WORD, source=probe)
                if accept is None:
                    accept = await_dialog_button(CONFIRM_WORD,
                                                 EXTENSION_RECHECK_SECONDS)
                if accept is not None:
                    say(f"  ...a dialog IS up and carries a {CONFIRM_WORD} "
                        f"button at {accept.centre} (conf {accept.conf:.0f}); "
                        f"its title would not read. Continuing on the button "
                        f"rather than aborting.")
                    shot = probe
        require(shot is not None, "no confirmation dialog appeared after Register")

        if undercut and not dry_run:
            wanted = price * max(1, qty)
            if dialog_shows_amount(shot, wanted):
                say(f"  the confirmation dialog also shows {wanted:,} Alz "
                    f"(advisory: this read cannot refuse, only agree).")
            else:
                say(f"  NOTE: could not read {wanted:,} back off the "
                    f"confirmation dialog; the panel checks above stand, but "
                    f"this one could not be taken.")
                record("undercut.dialog_unread", shot, price=price,
                       qty=qty, expected=wanted)

        alz_before_fee = None
        if not dry_run:
            try:
                alz_before_fee = get_alz(grab()) or None
            except Exception:
                alz_before_fee = None

        for step in range(1, MAX_CONFIRM_STEPS + 1):
            proof = grab()
            if dialog_kind(proof) != "confirm":
                break
            confirm = await_dialog_button(CONFIRM_WORD, timeout=4.0,
                                          source=proof)
            if confirm is None:
                break
            say(f"{CONFIRM_WORD} {step} at {confirm.centre} "
                f"(conf {confirm.conf:.0f})")
            if report is not None:
                report["committed"] = True
            click(*confirm.centre)
            committed = True
            time.sleep(0.8)

        if committed and report is not None:
            report["price"] = price
            report["qty"] = qty
            report["total"] = panel["net_sales"]
            report["committed"] = True

        if committed and not dry_run:
            note_registration(expect_item or "", price, qty)

            try:
                alz_after_fee = get_alz(grab()) or None
            except Exception:
                alz_after_fee = None
            if alz_before_fee and alz_after_fee and alz_before_fee > alz_after_fee:
                fee = alz_before_fee - alz_after_fee
                asking = (price or 0) * (qty or 0)
                if asking and fee > asking:
                    say(f"  the balance fell {fee:,} during the commit, more "
                        f"than the {asking:,} asked - not booking that as a "
                        "fee.")
                else:
                    say(f"  registration fee: {fee:,} Alz")
                    note_purchase(f"registration fee: {expect_item or 'item'}",
                                  0, fee, 0, note="Agent Shop registration fee")

        if await_dialog(None, timeout) is None:
            say(f"  a dialog is still on screen after {MAX_CONFIRM_STEPS} "
                f"confirm step(s); the shop-slot check below decides whether "
                f"the listing went through, and re-presses if it did not.")
            record("register.dialog_after_steps", steps=MAX_CONFIRM_STEPS)

        park_cursor()
        deadline = time.monotonic() + timeout
        reclicks = 0
        while time.monotonic() < deadline:
            shot = grab()
            after = read_register_panel(shot)
            if after["loaded"] and reclicks < CONFIRM_RECLICKS:
                again = dialog_button_band(CONFIRM_WORD, source=shot)
                if again is not None:
                    reclicks += 1
                    say(f"  the shop slot has not cleared and {CONFIRM_WORD} "
                        f"is still on screen at {again.centre}; pressing it "
                        f"again ({reclicks} of {CONFIRM_RECLICKS}).")
                    record("register.reconfirm", attempt=reclicks)
                    click(*again.centre)
                    park_cursor()
                    deadline = time.monotonic() + timeout
                    continue
            if not after["loaded"]:
                if report is not None:
                    report["price"] = price
                    report["qty"] = qty
                    report["total"] = panel["net_sales"]
                record("register.committed", row=row, col=col, price=price,
                       qty=qty, item=expect_item)
                landed = None
                if SHOP.ready and not expect_item:
                    say("  the row model cannot record a listing with no "
                        "identity; standing it down until the next full walk.")
                    SHOP.reset("registration without an identity")
                if SHOP.ready and expect_item:
                    try:
                        landed = SHOP.register(
                            expect_item, qty=qty, price=price,
                            floor=absolute_floor or 0, cost=cost_floor or 0)
                    except ShopDiverged as exc:
                        record("shopmodel.register_failed", reason=str(exc))
                        if SHOP.enforce:
                            raise
                        SHOP.reset("register into a full shop")
                say(f"Registered ({row},{col}) qty {qty} at {price:,} Alz "
                    f"each ({panel['net_sales']:,} total)."
                    + (f" Row {landed}." if landed else ""))
                return True
            time.sleep(0.5)
        require(False, "the shop slot did not clear after Confirmation")
        return False

    except Aborted as exc:
        record("register.aborted", reason=str(exc), row=row, col=col,
               item=expect_item, committed=committed)
        say(f"ABORTED: {exc}.")
        if committed:
            say("WARNING: Confirmation was already clicked, so the listing may "
                "have gone through. It will be checked against the table.")
            return False
        if dry_run:
            say("[dry run] leaving the screen exactly as it is. Nothing was listed.")
            return False
        if dialog_kind(grab()) is not None and not close_any_dialog():
            say("WARNING: a dialog is still open - dismiss it manually.")
        if read_register_panel(grab())["loaded"]:
            say("NOTE: the item is still sitting in the shop slot; "
                "run --clear to put it back in the inventory.")
        say("Nothing was listed.")
        return False


def relist(
    row: int,
    inv_row: int | None = None,
    absolute_row: "int | None" = None,
    inv_col: int | None = None,
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    attempts: int = RELIST_ATTEMPTS,
    expect: "RowRef | None" = None,
    work_tab_verified: bool = False,
) -> str:
    def say(message: str) -> None:
        if verbose:
            print(message)

    def close_shop() -> None:
        if dry_run:
            return
        try:
            if not shop_session_expired():
                age = shop_session_age() or 0.0
                say(f"Leaving the Agent Shop open ({age / 60:.1f} min into a "
                    f"{SHOP_SESSION_SECONDS / 60:g} min session).")
                return
            for _ in range(ESCAPE_ATTEMPTS):
                if not trade_window_open():
                    note_shop_closed()
                    return
                press_escape()
            if trade_window_open():
                say("Note: the Trade window would not close with Escape.")
            note_shop_closed()
        except Exception as exc:
            note_shop_closed()
            say(f"Note: could not close the Trade window ({exc}).")

    try:
        return _relist_cycle(row, inv_row, inv_col, dry_run, timeout,
                             verbose, attempts, say, expect,
                             work_tab_verified=work_tab_verified,
                             absolute_row=absolute_row)
    finally:
        close_shop()


def _relist_cycle(row, inv_row, inv_col, dry_run, timeout, verbose, attempts, say,
                  expect=None, work_tab_verified=False, absolute_row=None):
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            say(f"\n=== relist attempt {attempt}/{attempts} ===")

        if not dry_run:
            require_focus = focus_game()
            if not require_focus:
                say("Could not bring Cabal to the foreground.")
                return FAILED
            park_cursor()
            if not open_trade_window(verbose=verbose):
                say("Could not open the Agent Shop on the Register tab.")
                return FAILED
            if attempt == 1 and not work_tab_verified:
                if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
                    say("Aborting: the working inventory tab must be empty to "
                        "start.")
                    return FAILED
            if not wait_out_server_lag(verbose=verbose):
                say("The server is still not responding - giving this row "
                    "back rather than acting on a table it cannot serve.")
                return FAILED

            if not refresh_table(timeout=max(timeout, 20.0),
                                 verbose=verbose):
                say("Could not refresh the table - stopping.")
                return FAILED
            park_cursor()

        rows = await_rows(timeout)
        if not 1 <= row <= len(rows):
            say(f"Row {row} is out of range; {len(rows)} row(s) visible.")
            return FAILED

        target = rows[row - 1]

        if expect is not None:
            resolved, note = locate_row(rows, expect)
            if resolved is None:
                say(f"{expect.name!r} is no longer in the table "
                    f"({note or 'gone'}) - nothing was cancelled.")
                return FAILED
            if resolved.index != row:
                say(f"{expect.name!r} moved from row {row} to "
                    f"{resolved.index} since it was chosen - following it.")
                row = resolved.index
            target = resolved

        record("table.target", row=row, name=target.name, action=target.action,
               price=target.price, qty=target.qty, visible=len(rows),
               attempt=attempt,
               table=[[r.index, r.name, r.action, r.price, r.qty] for r in rows])

        if target.action == "receive":
            say(f"Row {row} is sold ({target.name!r}) - clicking Receive.")
            forget_unlisted()
            forget_range_view()
            if not dry_run and target.empties_on_collect:
                _known_rows = cached_rows_used()
                if _known_rows is not None:
                    note_rows_used(max(0, _known_rows - 1))
            if dry_run:
                say("[dry run] would click Receive, then relist any remainder")
                return RELISTED
            if attempt == attempts:
                say(f"Still sold on the final attempt ({attempts}) - stopping.")
                return FAILED

            try:
                alz_before = get_alz(grab()) or None
            except Exception:
                alz_before = None

            click(*target.change)
            park_cursor()

            accept = await_dialog_button(RECEIPT_WORD, timeout=6.0)
            if accept is None:
                say("The Confirm Receipt dialog did not appear - stopping.")
                return FAILED
            say(f"Confirm Receipt: accepting at {accept.centre} "
                f"(conf {accept.conf:.0f})")
            click(*accept.centre)
            time.sleep(1.0)
            if await_dialog(None, timeout) is None:
                say("The Confirm Receipt dialog stayed open - stopping.")
                return FAILED

            SHOP.collect(absolute_row or row,
                         empties=target.empties_on_collect,
                         qty_left=target.qty)

            _settle = time.monotonic() + RECEIVE_WAIT
            if alz_before:
                while time.monotonic() < _settle:
                    _now = get_alz(grab()) or 0
                    if _now > alz_before:
                        break
                    time.sleep(0.2)
            else:
                time.sleep(RECEIVE_WAIT)

            proceeds = None
            reject = ""
            try:
                alz_after = get_alz(grab()) or None
                if alz_before and alz_after and alz_after > alz_before:
                    proceeds = alz_after - alz_before
            except Exception:
                proceeds = None

            listed_units = registered_qty(target.name, target.price)
            reject = sale_rejection(proceeds, target.price, target.qty,
                                    listed_units)
            if reject:
                say(f"  the Alz balance moved {proceeds:,}, which cannot be "
                    f"right for this listing: {reject}. Counting the sale but "
                    "not the figure.")
                record("sale.implausible", item=target.name,
                       proceeds=proceeds, why=reject,
                       listed_units=listed_units, still_listed=target.qty)
                proceeds = None
            note_sale(target.name, target.price, proceeds, reject)
            if proceeds:
                sold = (proceeds // target.price
                        if target.price and proceeds % target.price == 0
                        else None)
                say(f"Collected {proceeds:,} Alz"
                    + (f" ({sold:,} x {target.price:,})" if sold else ""))
                say(f"  {sale_alert(target.name, sold, target.price, proceeds)}")
            else:
                say("Collected (the Alz balance did not read, so this sale is "
                    "counted but not measured).")

            if not wait_for_table(max(timeout, 20.0)):
                say("The table did not finish refreshing after Receive - stopping.")
                return FAILED

            def family(table: list[Row]) -> list[Row]:
                return listing_family(table, target.name, target.price)

            def quantities(pool: list[Row]) -> list:
                return family_quantities(pool)

            before = quantities(family(rows))

            refresh_table(timeout=timeout, verbose=False)

            def collected(table: list[Row]) -> Row | None:
                if not 1 <= row <= len(table):
                    return None
                here = table[row - 1]
                if here.action != "change":
                    return None
                if (_canonical(here.name) != _canonical(target.name)
                        or here.price != target.price
                        or here.qty != target.qty):
                    return None
                return here

            after: list = []
            after_rows: list[Row] = []
            after_table: list[Row] = []
            saw_table = False
            deadline = time.monotonic() + max(timeout, TABLE_READ_BUDGET)
            while time.monotonic() < deadline:
                rows_now = read_rows(grab())
                if rows_now:
                    saw_table = True
                    after_table = rows_now
                    after_rows = family(rows_now)
                    after = quantities(after_rows)
                    if after != before or collected(rows_now) is not None:
                        break
                time.sleep(0.8)

            if not saw_table:
                say("The table could not be read while checking for a "
                    "remainder - stopping rather than assuming it sold out.")
                return FAILED

            lost, gained = collect_delta(before, after)

            if not lost and not gained:
                settled = collected(after_table)
                if settled is not None:
                    say(f"Collected. Row {row} went from Receive to Change "
                        f"with {settled.qty} still listed - relisting the "
                        f"remainder.")
                    continue

                priced = (f"at {target.price:,} " if target.price is not None
                          else "")
                shows = (after_table[row - 1].action
                         if 1 <= row <= len(after_table) else "nothing")
                say(f"The {target.name!r} listings {priced}are unchanged "
                    f"after collecting ({before}) and row {row} still shows "
                    f"{shows!r} - the click did not take; retrying.")
                continue

            if len(lost) == 1 and not gained:
                say(f"{target.name!r} is no longer in the table - fully sold "
                    "and collected.")
                note_fully_collected(target.name)
                if is_chaos_set(target.name):
                    if clear_cheapest_chaos_lot():
                        say("  retired the cheapest outstanding chaos lot.")
                    record("chaos.lot_retired", where="relist_collect",
                           item=target.name)
                return SOLD_OUT

            if len(lost) == 1 and len(gained) == 1:
                candidates = [r for r in after_rows if r.qty == gained[0]]
                if len(candidates) != 1:
                    say(f"A remainder of {gained[0]} appeared but "
                        f"{len(candidates)} rows carry it, so which one is the "
                        "remainder cannot be told - it will be picked up next "
                        "cycle rather than relisting the wrong stack.")
                    return FAILED
                row = candidates[0].index
                say(f"Partially sold: {lost[0]} -> {gained[0]} at row {row} "
                    "- relisting the remainder.")
                _known_rows = cached_rows_used()
                if _known_rows is not None:
                    note_rows_used(_known_rows + 1)
                continue

            say(f"The {target.name!r} listings changed in a way this collect "
                f"does not explain (was {before}, now {after}) - leaving them "
                "for the next cycle rather than acting on a table that moved "
                "underneath us.")
            return FAILED

        if target.action != "change":
            say(f"Row {row} shows '{target.action}', not 'Change' - nothing to relist.")
            return FAILED

        original = target.price
        if original is None:
            say(f"Could not read the current price of row {row} ({target.name!r}); "
                "refusing to relist without a price to sanity-check against.")
            return FAILED
        if original < MIN_PLAUSIBLE_PRICE:
            say(f"Row {row} ({target.name!r}) priced at {original:,} Alz, below "
                f"the {MIN_PLAUSIBLE_PRICE:,} plausibility floor - the price "
                "column was probably misread. Refusing to relist it.")
            return FAILED

        if target.name == "(empty)" or len(_floor_key(item_name(target.name))) < 6:
            say(f"Row {row}'s name did not read ({target.name!r}). The price "
                "floor is looked up from the name, so relisting without one "
                "could list a floored item unprotected. Refusing.")
            return FAILED

        max_qty = wants_max_quantity(target.name)
        say(f"[relist 1/2] row {row}: {target.name!r} at {original:,} Alz")
        say("             will relist at the lowest current market price")
        if max_qty:
            say("             quantity will be maximised")

        before = origin = start_tab = None
        if not dry_run and (inv_row is None or inv_col is None):
            focus_game()
            park_cursor()
            before = grab()
            origin = inventory_origin(before) or inventory_origin()
            if origin is None:
                say("The Inventory panel is not visible, so the returned item "
                    "could not be followed. Open it (or pass an explicit slot) "
                    "and rerun. Nothing has been cancelled yet.")
                return FAILED
            start_tab = active_inventory_tab(before, origin)
            if start_tab is None:
                say("Could not tell which inventory tab is open, so the "
                    "returned items could not be followed reliably. "
                    "Nothing has been cancelled yet.")
                return FAILED
            if start_tab != WORK_TAB:
                say(f"Inventory tab {start_tab} is open, but the work tab is "
                    f"{WORK_TAB} - the emptiness check and the returned-item "
                    "diff would be looking at different tabs. Nothing has been "
                    "cancelled yet.")
                return FAILED
            record("inventory.before_cancel", tab=start_tab, origin=str(origin))
            say(f"Inventory tab {start_tab} is open; will return to it after "
                "cancelling.")

        cancel_report: dict = {}
        if not cancel_item(row, absolute_row=absolute_row,
                           dry_run=dry_run, timeout=timeout, verbose=verbose,
                           expect=RowRef.of(target, rows),
                           report=cancel_report):
            if game_disconnected():
                say("The game has DISCONNECTED - the client is showing 'You "
                    "have been disconnected from the server'. Nothing below "
                    "is a script fault; log back in and start again.")
                return FAILED

            if cancel_report.get("committed") is False and attempt < attempts:
                say("The cancel did not commit - nothing was withdrawn and the "
                    "listing is untouched, so this row can be tried again.")
                left = (dialog_button_band(DISMISS_WORD)
                        or dialog_button_band(CONFIRM_WORD))
                if left is not None:
                    say(f"  a dialog is still open over the table at "
                        f"{left.centre}; closing it before retrying.")
                    record("relist.retry_dialog_left_open",
                           row=row, at=str(left.centre))
                    close_any_dialog()
                continue

            say("Cancel did not complete - see above for what state it left. "
                "Nothing further will be listed this cycle.")
            return FAILED

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
            record("relist.stranded", stage="table_refresh", row=row,
                   item=target.name, qty=target.qty, tab=start_tab)
            say("The table did not finish refreshing after the cancel.")
            say(f"IMPORTANT: row {row} was already cancelled, so "
                f"{target.name!r} x{target.qty} is in inventory tab "
                f"{start_tab}, UNLISTED. Later cycles will fail their "
                "empty-work-tab check until it is cleared.")
            return FAILED

        slot = (inv_row, inv_col) if inv_row and inv_col else None
        if slot is None:
            if dry_run:
                say("[dry run] would locate the returned item by diffing the inventory")
                slot = (1, 1)
            else:
                if not select_inventory_tab(start_tab, origin):
                    say(f"Could not return to inventory tab {start_tab}.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is in your inventory, unlisted.")
                    return FAILED
                park_cursor()

                after = grab()
                record("inventory.after_cancel", after, tab=start_tab)
                returned = changed_slots(before, after, origin)
                if len(returned) > 1 and SHOP.enforce and SHOP.ready:
                    say(f"  row model: {len(returned)} slot(s) changed, but "
                        f"only {target.name!r} was cancelled; taking the "
                        "lowest.")
                    returned = returned[:1]
                if not returned:
                    record("inventory.diff_empty", after, tab=start_tab)
                    say(f"Could not tell which slot on tab {start_tab} "
                        f"{target.name!r} returned to, so refusing to list an "
                        "unidentified item.\n"
                        f"IMPORTANT: row {row} has already been cancelled - "
                        f"{target.name!r} is sitting in your inventory, unlisted.\n"
                        "Re-list it with: --register INV_ROW INV_COL")
                    return FAILED
                where = ", ".join(f"{r},{c}" for r, c in returned)
                record("inventory.returned", after, tab=start_tab, slots=where,
                       count=len(returned), taking=f"{returned[0]}")
                say(f"{target.name!r} returned to {len(returned)} slot(s) on "
                    f"tab {start_tab}: {where}")
                slot = returned[0]

        say(f"\n[relist 2/2] listing inventory slot ({slot[0]},{slot[1]})")
        report: dict = {}
        row_floor, lot_id = chaos_row_floor(
            target.name, target.qty or 0, original or 0,
            rank=next_chaos_rank() if is_chaos_set(target.name) else None)
        if row_floor:
            say(f"Chaos row floor: {row_floor:,} Alz "
                f"({target.qty} x {row_floor // max(1, target.qty or 1):,} paid)")
        listed = register_item(*slot, dry_run=dry_run,
                               timeout=timeout, verbose=verbose,
                               floor_price=original, maximise_qty=max_qty,
                               cost_floor=row_floor,
                               undercut=(CHAOS_UNDERCUT
                                         if is_chaos_set(target.name) else 0),
                               expect_item=target.name, expect_qty=target.qty,
                               report=report)
        if not listed and not report.get("committed"):
            record("relist.stranded", stage="register_failed", row=row,
                   item=target.name, qty=target.qty, tab=start_tab)
            say(f"\nIMPORTANT: row {row} was cancelled, so {target.name!r} "
                f"x{target.qty} is now in inventory tab {start_tab}, UNLISTED.")
            say("Every later cycle will fail its empty-work-tab check until "
                "that is cleared, so the run will stop after "
                f"{MAX_CONSECUTIVE_FAILURES} of them.")
            say(f"Re-list it by hand with:  --register INV_ROW INV_COL")
            return FAILED
        if dry_run:
            return RELISTED
        if not listed:
            say("The listing was committed before the failure, so it is on the "
                "market. Verifying it against the table rather than assuming.")

        found: dict = {}
        if SHOP.enforce and SHOP.ready:
            say("  row model: landing row already predicted and committed; "
                "not re-reading the table to confirm it.")
        elif not sanity_check(target.name, report.get("price"),
                              report.get("qty"),
                              timeout=timeout, verbose=verbose, found=found):
            bad = found.get("row")
            if bad is None:
                say(f"Could not verify the listing for {target.name!r}. "
                    "Nothing was withdrawn; will be checked again next cycle.")
                return FAILED
            def money(value: int | None) -> str:
                return f"{value:,}" if isinstance(value, int) else "an unreadable price"

            say(f"Withdrawing the mismatched listing on row {bad.index} "
                f"({bad.name!r})...")
            try:
                withdrawn = cancel_item(bad.index, expect=RowRef.of(bad, [bad]),
                                        timeout=timeout, verbose=verbose)
            except FatalAbort:
                raise
            except Exception as exc:
                raise FatalAbort(
                    f"listed {bad.name!r} at {money(bad.price)}, which does not "
                    f"match what was registered, AND the withdrawal itself "
                    f"failed ({type(exc).__name__}: {exc}). It is still on the "
                    "shop - remove it by hand."
                ) from exc
            if withdrawn:
                raise FatalAbort(
                    f"listed {bad.name!r} at {money(bad.price)} which does not "
                    f"match the {money(report.get('price'))} that was "
                    "registered. It has been withdrawn from the shop and the "
                    "run stopped."
                )
            raise FatalAbort(
                f"listed {bad.name!r} at {money(bad.price)}, which does not "
                f"match what was registered, AND it could not be withdrawn. It "
                "is still on the shop - remove it by hand."
            )
        return RELISTED

    say(f"Gave up after {attempts} attempts - the listing kept coming back sold.")
    return FAILED


ALL_ROWS_SPEC = "all"


def wants_all_rows(specs: list[str] | None) -> bool:
    return bool(specs) and len(specs) == 1 and \
        str(specs[0]).strip().casefold() == ALL_ROWS_SPEC


def parse_row_spec(specs: list[str]) -> list[int]:
    if wants_all_rows(specs):
        return []
    rows: list[int] = []
    for spec in specs:
        for chunk in str(spec).replace(",", " ").split():
            if "-" in chunk[1:]:
                lo, _, hi = chunk.partition("-")
                start, stop = int(lo), int(hi)
                if start > stop:
                    raise ValueError(f"range {chunk!r} runs backwards")
                rows.extend(range(start, stop + 1))
            else:
                rows.append(int(chunk))
    seen: set[int] = set()
    return [r for r in rows if not (r in seen or seen.add(r))]


def sanity_check(
    name: str,
    price: int | None,
    qty: int | None,
    timeout: float = 8.0,
    verbose: bool = True,
    found: dict | None = None,
    expect_at_least: int | None = None,
) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    record("sanity.start", name=name, price=price, qty=qty)
    if not refresh_table(timeout=max(timeout, 20.0), verbose=verbose):
        say("  could not refresh the table to check the listing.")
        return False
    park_cursor()

    rows = await_rows(timeout)
    if not rows:
        say("  the table could not be read.")
        return False
    ref = RowRef(name, qty, price)
    changeable = [r for r in rows if r.action == "change"]

    witnesses = [r for r in changeable
                 if _canonical(r.name) == _canonical(name)
                 and (price is None or r.price == price)
                 and (qty is None or r.qty is None or r.qty == qty)]
    if expect_at_least is not None and len(witnesses) < expect_at_least:
        say(f"  {len(witnesses)} row(s) of {name!r} at {price} on the board, "
            f"but {expect_at_least} should be by now - this round's listing "
            f"is not there.")
        record("sanity.short_count", name=name, price=price,
               seen=len(witnesses), wanted=expect_at_least)
        return False

    if witnesses:
        say(f"  row {witnesses[0].index} matches what was registered"
            + (f" ({len(witnesses)} identical rows)" if len(witnesses) > 1 else "")
            + ".")
        return True

    listed, note = locate_row(changeable, ref, strict=True)
    if listed is None:
        if note == "ambiguous":
            say(f"  several rows are named {name!r} and none carries the price "
                "and quantity just registered, so the new listing cannot be "
                "identified. Not withdrawing anything.")
            return False
        if len(rows) >= EXPECTED_ROWS:
            say(f"  {name!r} is not on the first screen, and the table is full "
                f"({len(rows)} rows) so the listing may be below it. Treating "
                f"as unverified-but-not-wrong rather than raising a false "
                f"alarm.")
            record("sanity.below_screen", name=name, price=price, qty=qty)
            return True

        say(f"  {name!r} is not in the table after relisting it.")
        suspect = [r for r in changeable if price is not None and r.price == price]
        if suspect:
            say("  these rows carry the price just registered: "
                + ", ".join(f"row {r.index} ({r.name!r})" for r in suspect[:4]))
            say("  that is most likely this listing with a misread name. "
                "Nothing has been withdrawn.")
        else:
            say("  nothing carries that price either - the listing may have "
                "sold, or may never have been created.")
        say("  CHECK THE SHOP BY HAND before the next run: if the wrong item "
            "was listed, it is still on the market.")
        return False
    say(f"  row {listed.index} names {listed.name!r} - matches what was relisted.")

    if price is not None and listed.price is None:
        say("  the listed price could not be read, so this cannot be verified.")
        return False
    if qty is not None and listed.qty is None:
        say("  the listed quantity could not be read; checking the price only.")

    if qty is not None and listed.qty is not None and listed.qty != qty:
        say(f"  note: quantity reads {listed.qty}, expected {qty} - not acting "
            "on that; the quantity column is not reliable enough to withdraw on.")

    problems = []
    if price is not None and listed.price is not None and listed.price != price:
        problems.append(f"price is {listed.price:,}, expected {price:,}")

    if problems:
        say(f"  possible mismatch on row {listed.index}: " + "; ".join(problems))
        say("  re-reading to confirm before withdrawing anything...")
        time.sleep(1.0)
        recheck = await_rows(timeout)
        again, _ = locate_row([r for r in recheck if r.action == "change"],
                              ref, strict=True)
        if again is None:
            say("  the row could not be identified on the re-read; "
                "not withdrawing.")
            return False
        if again.price is None:
            say("  the price could not be read on the re-read; not withdrawing.")
            return False
        if again.price < MIN_PLAUSIBLE_PRICE:
            say(f"  the re-read price {again.price:,} is below the "
                f"{MIN_PLAUSIBLE_PRICE:,} plausibility floor, so it is a "
                "misread rather than a mismatch; not withdrawing.")
            return False
        if again.price == price:
            say(f"  second read agrees with what was registered "
                f"({again.price:,}) - the first read was a misread.")
            return True
        say(f"  MISMATCH confirmed on row {again.index} ({again.name!r}): "
            f"price {again.price:,}, quantity {again.qty}")
        if found is not None:
            found["row"] = again
        return False

    say(f"  row {listed.index}: {listed.name!r} at {listed.price:,} Alz"
        + (f" x{listed.qty}" if listed.qty is not None else "") + " - matches.")
    return True


def ensure_shop_ready(verbose: bool = True) -> bool:
    if not focus_game():
        if verbose:
            print("Could not bring Cabal to the foreground.")
        return False
    park_cursor()
    return open_trade_window(verbose=verbose)


def relist_rows(
    rows: list[int],
    dry_run: bool = False,
    timeout: float = 8.0,
    verbose: bool = True,
    all_rows: bool = False,
) -> bool:

    forget_range_view()
    def say(message: str) -> None:
        if verbose:
            print(message)

    if (SHOP.enforce or SHOP_MODEL_SHADOW) and not SHOP.ready:
        mode = "ENFORCING" if SHOP.enforce else "SHADOW: records, never acts"
        say(f"Row model ({mode}) - walking the whole shop once to seed it "
            f"(all {SHOP_ROW_CAPACITY} slots); later cycles answer from it.")
        seed = shop_listing_pairs(timeout=timeout, verbose=verbose)
        if seed and max(i for i, _ in seed) >= SHOP_ROW_CAPACITY:
            SHOP.adopt(seed)
            note_range_view(SHOP_ROW_CAPACITY, seed)
            say(SHOP.describe())
        else:
            covered = max((i for i, _ in seed), default=0) if seed else 0
            say(f"  NOT seeded: the walk reached row {covered} of "
                f"{SHOP_ROW_CAPACITY}. The model stays off for this run.")
            record("shopmodel.seed_failed", covered=covered)

    if not dry_run:
        if not ensure_shop_ready(verbose=verbose):
            say("Could not open the Agent Shop to read the listings.")
            return False
        if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
            if carried_total() > 0:
                for slot in carried_slots():
                    owed = carried_sets(slot)
                    say(f"The work tab holds {owed} carried Set(s) for "
                        f"{FAVOURITE_SLOTS.get(slot, slot)!r} from an earlier "
                        f"resupply. Converting and listing them before "
                        f"anything else, rather than failing the cycle.")
                    record("relist.carry_recovery", slot=slot, carried=owed)
                    try:
                        restock_core(slot, target=BUY_MAXIMUM, verbose=verbose)
                    except Exception as exc:
                        say(f"  the carry recovery for slot {slot} did not "
                            f"complete: {exc}")
                        record("relist.carry_recovery_failed", slot=slot,
                               why=str(exc))
                if not ensure_shop_ready(verbose=verbose):
                    say("Could not reopen the Agent Shop after the carry "
                        "recovery.")
                    return False

            if chaos_stranded() and CHAOS_ENABLED:
                say("The work tab holds goods from a chaos pass that did not "
                    "finish. Crafting and listing them before anything else.")
                record("relist.chaos_recovery")
                try:
                    chaos_pass(timeout=timeout, verbose=verbose,
                               scope=None if all_rows else list(rows))
                except Exception as exc:
                    say(f"  the chaos recovery did not complete: {exc}")
                    record("relist.chaos_recovery_failed", why=str(exc))
                if not ensure_shop_ready(verbose=verbose):
                    say("Could not reopen the Agent Shop after the chaos "
                        "recovery.")
                    return False

            if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
                say("Aborting: the working inventory tab must be empty to "
                    "start.")
                return False

        reset_chaos_ranks()

        if CHAOS_ENABLED:
            chaos_pass(timeout=timeout, verbose=verbose,
                       scope=None if all_rows else list(rows))

            if not ensure_shop_ready(verbose=verbose):
                say("The Agent Shop did not reopen after the chaos pass; "
                    "closing anything left over and trying once more.")
                try:
                    close_npc_shop(verbose=verbose)
                except Exception:
                    pass
                if not ensure_shop_ready(verbose=verbose):
                    say("Still could not reopen the Agent Shop after chaos - "
                        "nothing can be relisted without it.")
                    return False

            if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
                say("Aborting: the chaos pass left items in the working "
                    "inventory tab, so nothing below can diff it safely.")
                return False

        if RESTOCK_BEFORE_RELIST:
            restock_pass(timeout=timeout, verbose=verbose,
                         scope=None if all_rows else list(rows))
            if not ensure_shop_ready(verbose=verbose):
                say("The Agent Shop did not reopen after the resupply; "
                    "closing anything left over and trying once more.")
                try:
                    close_npc_shop(verbose=verbose)
                except Exception:
                    pass
                if not ensure_shop_ready(verbose=verbose):
                    say("Still could not reopen the Agent Shop - nothing can "
                        "be relisted without it.")
                    return False
            if restock_is_armed() and not ensure_work_tab_empty(
                    timeout=timeout, verbose=verbose):
                say("Aborting: the resupply left something in the work tab.")
                return False


    snapshot = await_rows(timeout)
    if not snapshot:
        say("No listings visible - is the Trade window open on the Register tab?")
        return False

    added_rows: set[int] = set()

    beyond = [i for i in rows if i > len(snapshot)]
    scrolling = bool(beyond) or all_rows

    if scrolling and not all_rows and snapshot and SHOP.enforce and SHOP.ready:
        occupied_beyond = [i for i in beyond if not SHOP.is_empty(i)]
        if not occupied_beyond:
            say(f"Row model: rows {min(beyond)}-{max(beyond)} are empty, so "
                f"there is nothing to walk to.")
            record("relist.no_walk_needed", asked=max(beyond), source="model")
            rows = [i for i in rows if i <= len(snapshot)]
            beyond = []
            scrolling = False
            if not rows:
                say("None of the rows asked for hold anything; nothing to do "
                    "this cycle.")
                return True

    chaos_tries = 0
    chaos_capped = False
    targets: list[tuple[int, RowRef, str]] = []
    core_rows_left: dict[int, int] = {}

    if scrolling:
        if all_rows:
            say("Relisting EVERY listing in the shop; sweeping it to see how "
                "many there are.")
        else:
            say(f"Row(s) {', '.join(str(i) for i in beyond)} are past the "
                f"first screen of {len(snapshot)}; enumerating the whole shop.")
        need = SHOP_ROW_CAPACITY if all_rows else max(beyond)
        listings = cached_range_view(need)
        if listings is not None:
            say(f"  reusing this cycle's walk of rows 1-{need} rather than "
                f"sweeping the shop a second time.")
        else:
            listings = enumerate_listings(
                timeout=timeout, verbose=verbose,
                stop_after=None if all_rows else need)
            if listings:
                note_range_view(max(i for i, _ in listings), listings)
        if listings is None:
            say("The shop could not be enumerated, so rows past the first "
                "screen cannot be addressed safely - stopping rather than "
                "acting on a position that might be the wrong listing.")
            return False
        catalogue = [row for _, row in listings]
        core_rows_left = {slot: n
                          for slot, n in core_row_counts(catalogue).items()
                          if slot in enabled_buying_slots()}

        note_rows_used(rows_in_use(catalogue))

        if all_rows:
            targets = [(index, RowRef.of(row, catalogue), row.action)
                       for index, row in listings]
            live = sum(1 for _, _, a in targets
                       if a in ("change", "receive"))
            say(f"Found {len(targets)} slot(s), {live} of them live.")
        else:
            by_index = dict(listings)
            for index in rows:
                row = by_index.get(index)
                if row is None:
                    if index in added_rows:
                        continue
                    say(f"Row {index} is out of range; the shop holds "
                        f"{len(listings)} listing(s).")
                    return False
                targets.append((index, RowRef.of(row, catalogue), row.action))
    else:
        for index in rows:
            if not 1 <= index <= len(snapshot):
                if index in added_rows:
                    continue
                say(f"Row {index} is out of range; {len(snapshot)} row(s) visible.")
                return False
            row = snapshot[index - 1]
            targets.append((index, RowRef.of(row, snapshot), row.action))

    if targets:
        last_live = 0
        for position, (_, _, action) in enumerate(targets, start=1):
            if action in ("change", "receive"):
                last_live = position
        if last_live and last_live < len(targets):
            trimmed = targets[last_live:]
            targets = targets[:last_live]
            say(f"The shop has consolidated: the last {len(trimmed)} slot(s) "
                f"({trimmed[0][0]}-{trimmed[-1][0]}) are empty, so this batch "
                f"is rows {targets[0][0]}-{targets[-1][0]} instead.")
            record("batch.trimmed", kept=len(targets), dropped=len(trimmed),
                   last_live_row=targets[-1][0], first_empty=trimmed[0][0])

    say(f"Relisting {len(targets)} row(s), tracked by name, quantity and price:")
    for index, ref, action in targets:
        priced = f"{ref.price:,} Alz" if ref.price is not None else "price unread"
        say(f"  {index:2d}. [{action}] {ref.name} x{ref.qty} at {priced}")

    worked = 0

    failed_rows: list[str] = []
    handled: set = set()
    handled_by_name: dict = {}
    targets_by_name: dict = {}
    for _i, _ref, _a in targets:
        _k = _canonical(_ref.name)
        targets_by_name[_k] = targets_by_name.get(_k, 0) + 1
    forget_collected()

    for position, (index, ref, action) in enumerate(targets, start=1):
        name = ref.name

        if stop_requested():
            say("")
            say(f"{STOP_FILE.name} is present - stopping before the war-lag "
                f"wait, after {position - 1} of {len(targets)} row(s).")
            record("relist.stopped", reason="stop_file", done=position - 1)
            raise KeyboardInterrupt(f"{STOP_FILE.name} requested a stop")

        avoid_warlag(allowance=WAR_ROW_ALLOWANCE, verbose=verbose)

        if stop_requested():
            say("")
            say(f"{STOP_FILE.name} is present - stopping after "
                f"{position - 1} of {len(targets)} row(s).")
            record("relist.stopped", reason="stop_file",
                   done=position - 1)
            raise KeyboardInterrupt(
                f"{STOP_FILE.name} requested a stop")

        say(f"\n########## {position}/{len(targets)}: row {index} - {name!r} ##########")
        if SHOP.ready:
            say(SHOP.describe())
            say(f"  model says row {index} holds "
                f"{(SHOP.content(index) or {}).get('name', '(empty)')!r}; "
                f"a cancel here frees it and the relist lands at row "
                f"{SHOP.first_empty() if SHOP.is_empty(index) else min(index, SHOP.first_empty() or index)}")

        if action == "register" or name == "(empty)":
            say("Empty slot - nothing to relist, skipping.")
            continue

        if not dry_run and not ensure_shop_ready(verbose=verbose):
            say("Could not reopen the Agent Shop - stopping.")
            return False
        view_report: dict = {}
        live = None

        positional = (scrolling and SHOP.enforce and SHOP.ready
                      and 1 <= index <= ROW_INDEX_LIMIT and not dry_run)
        if positional:
            top = goto_row(index, timeout=timeout, verbose=verbose)
            if top is None:
                say(f"  row {index} could not be reached positionally; "
                    f"falling back to the identity search.")
                positional = False
            else:
                SHOP.check(index, top)
                live = [top]
                view_report["top_index"] = index

        if live is None:
            live = (bring_into_view(ref, timeout=timeout, verbose=verbose,
                                    hint=index, report=view_report)
                    if scrolling else await_rows(timeout))
        if not live:
            say("The listings could not be read - stopping rather than "
                "treating an unreadable table as an empty shop.")
            return False
        if CHAOS_ENABLED and not dry_run:
            top_index = view_report.get("top_index") if scrolling else 1
            shows_chaos = top_index == 1
            why = chaos_attention_needed(
                live, trust_count=((not scrolling) or shows_chaos)
                and not positional)
            if why and "sold" not in why:
                if not chaos_capped:
                    chaos_capped = True
                    on_board = sum(1 for r in chaos_shop_rows(live)
                                   if getattr(r, "action", None) == "change")
                    say(f"Chaos is {on_board} of {CHAOS_ROWS} - leaving the "
                        f"refill for the next cycle rather than stalling the "
                        f"batch; sold bundles are still collected on sight.")
                    record("chaos.midbatch_refill_deferred",
                           on_board=on_board, target=CHAOS_ROWS)
                why = ""
            if why:
                say(f"\nCHAOS TAKES PRIORITY over row {index}: {why}.")
                forget_range_view()
                chaos_pass(timeout=timeout, verbose=verbose,
                           scope=None if all_rows else list(rows))
                if not ensure_shop_ready(verbose=verbose):
                    say("The Agent Shop did not reopen after the chaos pass.")
                    return False
                if not ensure_work_tab_empty(timeout=timeout, verbose=verbose):
                    say("The chaos pass left items in the working inventory "
                        "tab; nothing below can diff it safely.")
                    return False
                say(f"Chaos handled; re-reading the table before row {index}.")
                view_report = {}
                live = (bring_into_view(ref, timeout=timeout, verbose=verbose,
                                        hint=index, report=view_report)
                        if scrolling else await_rows(timeout))
                if not live:
                    say("The listings could not be read after the chaos pass "
                        "- stopping rather than acting on a stale view.")
                    return False

        current = [r for r in live if r.action in ("change", "receive")]

        top_index = view_report.get("top_index")
        match, note = None, ""
        if ref.siblings > 1 and top_index is not None:
            offset = index - top_index
            if 0 <= offset < len(live):
                candidate = live[offset]
                if (candidate.action in ("change", "receive")
                        and _names_agree(candidate.name, ref.name)):
                    match = candidate
                    note = (f"one of {ref.siblings} identical stacks; taking "
                            f"row {index} by measured position")
                elif not match_rows(current, ref.name):
                    say(f"{name!r} is no longer in this part of the table - "
                        f"sold while the batch was running, skipping.")
                    match, note = None, "missing"
                else:
                    say(f"row {index} should be screen row {offset + 1} of "
                        f"this view but holds {candidate.name!r} "
                        f"({candidate.action}), and {name!r} is elsewhere in "
                        f"the view - the shop moved under the sweep. Stopping "
                        f"rather than relisting a row nobody named.")
                    return False
            elif not match_rows(current, ref.name):
                say(f"{name!r} is past the end of the table now - sold while "
                    f"the batch was running, skipping.")
                match, note = None, "missing"
            else:
                say(f"row {index} is outside the view that was walked to "
                    f"(offset {offset}), and {name!r} is still in the view - "
                    f"the shop moved under the sweep. Stopping rather than "
                    f"guessing which stack it is.")
                return False
        else:
            match, note = locate_row(current, ref)
        if match is None and note == "unmatched":
            say(f"{name!r} did not read clearly; looking again before giving "
                f"up the cycle.")
            time.sleep(TOOLTIP_CLEAR_SECONDS)
            retry_report: dict = {}
            again = (bring_into_view(ref, timeout=timeout, verbose=False,
                                     hint=index, report=retry_report)
                     if scrolling else await_rows(timeout))
            if again:
                current = [r for r in again
                           if r.action in ("change", "receive")]
                match, note = locate_row(current, ref)
                if match is not None:
                    say(f"  it read cleanly on the second look.")
                    record("relist.reread_rescued", item=name)
            if match is None and was_fully_collected(name):
                say(f"{name!r} was collected earlier in this batch and is "
                    f"gone; what is on screen are its siblings, whose names "
                    f"differ only by their count. Skipping it rather than "
                    f"stopping the cycle.")
                record("relist.collected_midbatch", item=name)
                match, note = None, "missing"
            elif match is None:
                say(f"{name!r} is on the table but its name did not read "
                    "clearly enough to act on, twice. Stopping rather than "
                    "skipping a live listing as though it had sold.")
                record("relist.unmatched", item=name)
                return False
        if match is None:
            say(f"{name!r} is no longer in the table - already sold out, skipping.")
            gone_slot = favourite_for(name)
            if gone_slot in core_rows_left:
                core_rows_left[gone_slot] = max(
                    0, core_rows_left[gone_slot] - 1)
                if (RESTOCK_MID_CYCLE and not dry_run
                        and core_rows_left[gone_slot] <= RESTOCK_AT_OR_BELOW_ROWS
                        and restock_is_armed()
                        and position < len(targets)):
                    left = len(targets) - position
                    say("")
                    say(f"{FAVOURITE_SLOTS[gone_slot]!r} has just "
                        f"sold out - no rows of it are left in the "
                        f"shop. Ending this batch here so it can be "
                        f"resupplied now rather than after the "
                        f"remaining {left} row(s); they are relisted "
                        f"on the next cycle, which starts "
                        f"immediately.")
                    record("relist.mid_cycle_restock", slot=gone_slot,
                           item=FAVOURITE_SLOTS[gone_slot],
                           rows_left=left, done=position)
                    return True
            continue
        def _absolute(view_row):
            if top_index is None:
                return view_row.index
            return top_index + view_row.index - 1

        same_name = [r for r in current
                     if _canonical(r.name) == _canonical(name)]
        canon = _canonical(name)
        done_for_name = handled_by_name.get(canon, 0)
        allowed = max(targets_by_name.get(canon, 1), len(same_name))
        if done_for_name >= allowed:
            spare = [r for r in current
                     if _absolute(r) not in handled
                     and _canonical(r.name) == _canonical(name)]
            if spare:
                say(f"  row {_absolute(match)} was already relisted this "
                    f"cycle; taking row {_absolute(spare[0])} instead -- "
                    "identical stacks cannot be told apart by name.")
                record("relist.sibling_collision", item=name,
                       already=_absolute(match), taking=_absolute(spare[0]))
                match = spare[0]
            elif match.action == "receive":
                say(f"  every row matching {name!r} has been handled this "
                    f"cycle, but row {_absolute(match)} is SOLD and its Alz "
                    f"is still on the table - collecting it. A collect cannot "
                    f"be done twice: the row is empty afterwards.")
                record("relist.receive_past_collision", item=name,
                       row=_absolute(match))
            else:
                say(f"  every row matching {name!r} has already been relisted "
                    "this cycle; skipping rather than doing one twice.")
                record("relist.sibling_exhausted", item=name,
                       already=sorted(handled))
                continue
        handled.add(_absolute(match))
        handled_by_name[canon] = handled_by_name.get(canon, 0) + 1

        if note:
            say(f"  {note}")
        if match.index != index:
            say(f"Moved: now at row {match.index}.")

        outcome = relist(match.index, dry_run=dry_run, timeout=timeout,
                         verbose=verbose, expect=ref,
                         work_tab_verified=(position == 1),
                         absolute_row=index)
        if outcome == SOLD_OUT:
            worked += 1
            say(f"{name!r} sold out - collected, nothing to relist. Moving on.")
            continue
        if outcome != RELISTED:
            left = len(targets) - position
            if not dry_run and require_empty_work_tab(verbose=False):
                if dialog_present():
                    say("A dialog is still open after that failure; backing "
                        "out of it before continuing.")
                    if not close_any_dialog():
                        say("...it would not close, and it covers the table, "
                            "so every later row would fail its read - "
                            "stopping instead.")
                        say(f"Relisting {name!r} failed - stopping; "
                            f"{left} row(s) not attempted.")
                        return False
                say(f"Relisting {name!r} failed, but inventory tab {WORK_TAB} "
                    f"is clean, so the failure is confined to this row - "
                    f"continuing with {left} row(s) still to go.")
                failed_rows.append(name)
                continue
            if not dry_run and left:
                say(f"Relisting {name!r} failed AND inventory tab {WORK_TAB} is "
                    "not clean, so every later row would fail the same way.")
            say(f"Relisting {name!r} failed - stopping; "
                f"{len(targets) - position} row(s) not attempted.")
            return False
        worked += 1

        if not dry_run and not wait_for_table(max(timeout, 20.0)):
            left = len(targets) - position
            if not left:
                break
            if require_empty_work_tab(verbose=False):
                say(f"The table did not finish refreshing after {name!r}, but "
                    f"the relist completed and inventory tab {WORK_TAB} is "
                    f"clean - continuing with {left} row(s) still to go.")
                continue
            say(f"The table did not finish refreshing after {name!r} AND "
                f"inventory tab {WORK_TAB} is not clean - stopping.")
            return False

    actionable = sum(1 for _, _, action in targets
                     if action in ("change", "receive"))

    if not dry_run and not actionable:
        say(f"\nAll {len(targets)} row(s) read as empty slots. Re-reading to "
            "be sure before treating the shop as sold out...")
        asked_all = {index for index, _, _ in targets}
        if asked_all and max(asked_all) > SCREEN_ROWS:
            pairs = enumerate_listings(timeout=timeout, verbose=False,
                                       stop_after=max(asked_all))
            again = (None if pairs is None
                     else [_dc.replace(r, index=i) for i, r in pairs])
        else:
            again = await_rows(timeout)
        if not again:
            say("  the re-read could not be read at all - treating this as a "
                "failed cycle rather than a sold-out shop.")
            return False
        asked = asked_all
        live_now = [r for r in again
                    if r.action in ("change", "receive") and r.index in asked]
        elsewhere = [r for r in again
                     if r.action in ("change", "receive")
                     and r.index not in asked]

        if live_now:
            say(f"  the re-read found {len(live_now)} live row(s) in rows "
                f"{min(asked)}-{max(asked)} after all - the first read caught "
                "the table mid-refresh. Failing this cycle so it retries.")
            return False

        if elsewhere:
            raise ShopIdle(
                f"rows {min(asked)}-{max(asked)} are all empty and nothing "
                f"refilled them, but {len(elsewhere)} row(s) outside the batch "
                f"are still live - so the shop has not sold out. Waiting for "
                f"the next cycle rather than counting this as a failure")

        if CHAOS_ENABLED and CHAOS_HELD_OFF:
            raise ShopIdle(
                "every row is empty and the chaos pass listed nothing this "
                "cycle, so the shelf is waiting rather than sold out. Not "
                "ending a run on a pass that declined for its own reasons")

        if CHAOS_ENABLED and CHAOS_HELD_OFF_ON_LANDING:
            raise ShopIdle(
                "every row is empty and chaos is holding off: the next "
                "registration would land below this batch's rows, where "
                "nothing would ever reprice it. Waiting rather than ending "
                "the run")

        if CHAOS_ENABLED and CHAOS_HELD_OFF_ON_MARGIN:
            raise ShopIdle(
                f"every row is empty and chaos is holding off: margin "
                f"{CHAOS_HELD_OFF_MARGIN:,} is under the "
                f"{CHAOS_MARGIN_FLOOR:,} floor. Waiting for the spread rather "
                f"than ending the run")

        raise ShopEmpty(
            f"every one of the {len(targets)} row(s) is an empty slot - "
            f"the shop has sold out, so there is nothing left to relist")

    if actionable and not worked:
        say(f"\nNone of the {actionable} live row(s) were relisted - every one "
            "read as already sold. That is not a successful cycle; stopping so "
            "it is not reported as one.")
        return False

    if failed_rows:
        say(f"\n{len(failed_rows)} row(s) failed and were skipped: "
            + ", ".join(repr(n) for n in failed_rows))
        say("Each was confined to its own row - the work tab stayed clean, so "
            "the rest of the batch continued. They will be retried next cycle.")

    if not RESTOCK_BEFORE_RELIST and not dry_run:
        restock_pass(timeout=timeout, verbose=verbose)
    say(f"\nAll {len(targets)} row(s) processed"
        + (f" ({worked} relisted" if worked else " (none relisted")
        + (f", {len(failed_rows)} failed)." if failed_rows else ")."))

    return True


def craft_window_open(source: "Image.Image | None" = None) -> bool:
    shot = source if source is not None else grab()
    words = {w.text.casefold()
             for w in find_words(shot, CRAFT_WINDOW_REGION, 20) if w.conf >= 55}
    return "material" in words and ("complete" in words or "request" in words)


def chaos_cores_held(verbose: bool = True) -> int:
    def say(message: str) -> None:
        if verbose:
            print(message)

    try:
        leave_shop(verbose=False)
        time.sleep(0.5)
        if not open_craft_window(timeout=6.0, verbose=False):
            return 0
        entry = CRAFT_RECIPES.get(int(CHAOS_RECIPE or 1))
        if entry is None:
            press_escape()
            return 0
        tier, recipe, _label = entry
        click(*tier)
        time.sleep(0.8)
        click(*recipe)
        time.sleep(1.2)
        held = craft_material_held(grab())
        if not held:
            time.sleep(CRAFT_MATERIAL_SETTLE)
            held = craft_material_held(grab())
        press_escape()
        time.sleep(0.4)
        return int(held or 0)
    except Exception as exc:
        say(f"  could not read what is already held ({exc}); assuming none.")
        try:
            press_escape()
        except Exception:
            pass
        return 0


def craft_material_held(source: "Image.Image | None" = None) -> "int | None":
    shot = source if source is not None else grab()
    words = [w.text for w in find_words(shot, CRAFT_MATERIAL_REGION, 20)
             if w.conf >= 50]
    match = _CRAFT_MATERIAL.search(" ".join(words))
    if match is None:
        return None
    return int(match.group(1))


CRAFT_OPEN_ATTEMPTS = 3


def open_craft_window(timeout: float = 8.0, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if craft_window_open():
        say("  the craft window is already open.")
    else:
        origin = inventory_origin()
        if origin is None and open_inventory(timeout=timeout, verbose=verbose):
            origin = inventory_origin()
        if origin is None:
            say("  the Inventory panel is not open and would not open, so the "
                "craft key cannot be reached.")
            record("chaos.no_inventory")
            return False
        if not select_inventory_tab(CHAOS_CRAFT_TAB, origin):
            say(f"  could not reach inventory tab {CHAOS_CRAFT_TAB}.")
            return False
        time.sleep(0.4)
        row, col = CHAOS_CRAFT_KEY_SLOT
        point = slot_centre_at(origin, row, col)

        here_now = active_inventory_tab(origin=origin)
        if here_now != CHAOS_CRAFT_TAB:
            say(f"  the Inventory is on tab {here_now}, not tab "
                f"{CHAOS_CRAFT_TAB} - REFUSING to right-click slot "
                f"({row},{col}), which on that tab is an ordinary item.")
            record("chaos.wrong_tab", wanted=CHAOS_CRAFT_TAB,
                   found=here_now if here_now is not None else "unreadable")
            return False

        opened = False
        for attempt in range(1, CRAFT_OPEN_ATTEMPTS + 1):
            say(f"  right-clicking the Remote Request Card at ({row},{col}) "
                f"{point}" + (f" (attempt {attempt})" if attempt > 1 else ""))
            right_click(*point)
            park_cursor(settle=TOOLTIP_CLEAR_SECONDS)
            deadline = time.monotonic() + max(2.0, timeout / CRAFT_OPEN_ATTEMPTS)
            while time.monotonic() < deadline:
                if craft_window_open():
                    opened = True
                    break
                time.sleep(0.4)
            if opened:
                break
        if not opened:
            try:
                for _ in range(ESCAPE_ATTEMPTS):
                    press_escape()
                    time.sleep(0.3)
                    if not dialog_present():
                        break
            except Exception as exc:
                say(f"  (could not clear a stray dialog: {exc})")
            say(f"  the Remote Request window did not open after "
                f"{CRAFT_OPEN_ATTEMPTS} attempt(s).")
            record("chaos.window_missing", attempts=CRAFT_OPEN_ATTEMPTS)
            return False

    origin = inventory_origin()
    if origin is None or not select_inventory_tab(CHAOS_WORK_TAB, origin):
        say(f"  could not put the Inventory on tab {CHAOS_WORK_TAB}, which is "
            f"where the crafted Sets have to land.")
        return False
    return True


def craft_chaos_sets(timeout: float = 8.0, verbose: bool = True) -> int:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not craft_window_open():
        say("  the craft window is not open.")
        return 0

    entry = CRAFT_RECIPES.get(int(CHAOS_RECIPE or 1))
    if entry is None:
        say(f"  chaos_recipe {CHAOS_RECIPE} is not a known recipe "
            f"({sorted(CRAFT_RECIPES)}); not crafting.")
        record("craft.recipe_unknown", setting=CHAOS_RECIPE)
        return 0
    tier, recipe, label = entry
    say(f"  selecting {label} (chaos_recipe {CHAOS_RECIPE}): tier at {tier}, "
        f"recipe at {recipe}")
    click(*tier)
    time.sleep(0.8)
    click(*recipe)
    time.sleep(1.2)
    record("craft.recipe_selected", setting=CHAOS_RECIPE,
           tier=str(tier), recipe=str(recipe))

    shot = grab()
    if not craft_window_open(shot):
        say("  the craft window went away while the recipe was being picked.")
        return 0

    before = craft_material_held(shot)
    if before is None:
        say("  the Required Material counter did not read - refusing to craft "
            "blind.")
        record("chaos.material_unread")
        return 0
    if before is not None and before < 1:
        for _ in range(QTY_READBACK_TRIES):
            time.sleep(CRAFT_MATERIAL_SETTLE)
            again = craft_material_held(grab())
            if again:
                say(f"  the material counter re-read as {again} (it was 0 a "
                    f"moment ago -- the recipe panel was still settling).")
                record("chaos.material_settled", first=before, then=again)
                before = again
                break
    if before is None or before < 1:
        say("  no Chaos Cores are held; nothing to craft.")
        record("chaos.material_zero")
        return 0
    say(f"  {before} Chaos Core(s) held")

    say("  Request All")
    click(*CRAFT_REQUEST_ALL)
    time.sleep(2.0)

    made = before

    settle = craft_settle_seconds(before)
    say(f"  waiting {settle:.0f}s for {made} craft(s) to finish "
        f"({craft_settle_rate():.0f}s per {CRAFT_SETTLE_BLOCK}, rounded up, "
        f"chaos_recipe {CHAOS_RECIPE})")
    time.sleep(settle)
    waited = settle
    say(f"  waited {waited:.0f}s")
    record("chaos.craft_settled", made=made, waited=round(waited, 1),
           budget=round(settle, 1))

    after = craft_material_held()
    if after is None:
        say(f"  the material counter did not read back; assuming the queue "
            f"took all {before}.")
    else:
        made = before - after
        say(f"  the queue consumed {made} of {before} Core(s).")

    origin = inventory_origin()
    if origin is not None and not select_inventory_tab(CHAOS_WORK_TAB, origin):
        say(f"  WARNING: could not put the Inventory back on tab "
            f"{CHAOS_WORK_TAB} before collecting; the crafted Sets will land "
            f"on whatever tab is showing.")
        record("chaos.craft_tab_lost", wanted=CHAOS_WORK_TAB)
    time.sleep(0.4)

    say("  Complete All")
    click(*CRAFT_COMPLETE_ALL)
    time.sleep(2.0)
    record("chaos.crafted", before=before, after=after, made=made)
    say(f"  crafted {made} Chaos Core Set(s).")
    return max(0, made)


def compress_stack(row: int, col: int, verbose: bool = True,
                   tab: "int | None" = None) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not open_inventory(verbose=verbose):
        say("  the Inventory panel is not open and would not open.")
        record("chaos.compress_no_panel")
        return False
    origin = inventory_origin()
    if origin is None:
        say("  the Inventory panel is open but its anchor would not read.")
        record("chaos.compress_no_anchor")
        return False

    if tab is not None:
        if not select_inventory_tab(tab, origin):
            say(f"  could not reach inventory tab {tab}; refusing to click a "
                f"slot number on the wrong tab.")
            record("chaos.compress_wrong_tab", tab=tab)
            return False
        time.sleep(0.4)
        origin = inventory_origin() or origin

    point = slot_centre_at(origin, row, col)
    if not _point_in_inventory_grid(*point):
        again = inventory_origin()
        if again is not None and again != origin:
            origin = again
            point = slot_centre_at(origin, row, col)
    if not _point_in_inventory_grid(*point):
        say(f"  slot ({row},{col}) resolves to {point}, which is not inside "
            f"the inventory grid - refusing to click it.")
        record("chaos.compress_bad_origin", point=str(point), tab=tab)
        return False
    say(f"  compressing at tab {tab if tab is not None else '?'} "
        f"({row},{col}) {point}")
    alt_click(*point)
    time.sleep(1.5)
    record("chaos.compressed", slot=f"{row},{col}", tab=tab)
    return True


_CHAOS_RANK = 0


def reset_chaos_ranks() -> None:
    global _CHAOS_RANK
    _CHAOS_RANK = 0


def next_chaos_rank() -> int:
    global _CHAOS_RANK
    rank = _CHAOS_RANK
    _CHAOS_RANK += 1
    return rank


def note_chaos_lot(unit_cost: int, listed_price: int, qty: int) -> None:
    if unit_cost < 1 or listed_price < 1:
        return
    conn = sales_db()
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO chaos_lots (unit_cost, listed_price, qty, created, "
            "run) VALUES (?,?,?,?,?)",
            (int(unit_cost), int(listed_price), int(max(1, qty)),
             _dt.datetime.now().isoformat(" ", "seconds"), run_id()))
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def chaos_lots_cheapest_first() -> list:
    conn = sales_db()
    if conn is None:
        return []
    try:
        return [(int(r[0]), int(r[1])) for r in conn.execute(
            "SELECT id, unit_cost FROM chaos_lots WHERE run = ? "
            "ORDER BY unit_cost ASC, id ASC", (run_id(),)).fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def chaos_lots() -> list:
    conn = sales_db()
    if conn is None:
        return []
    try:
        return [tuple(r) for r in conn.execute(
            "SELECT id, unit_cost, listed_price FROM chaos_lots WHERE run = ? "
            "ORDER BY unit_cost DESC, id ASC", (run_id(),)).fetchall()]
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def clear_cheapest_chaos_lot() -> bool:
    lots = chaos_lots_cheapest_first()
    if not lots:
        return False
    conn = sales_db()
    if conn is None:
        return False
    try:
        conn.execute("DELETE FROM chaos_lots WHERE id = ?", (lots[0][0],))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def is_chaos_set(name: str) -> bool:
    wanted = _floor_key(item_name(FAVOURITE_SLOTS.get(CHAOS_SET_SLOT, "")))
    if not wanted:
        return False
    return _floor_key(item_name(_PACK_ANYWHERE.sub(" ", name or ""))) == wanted


def chaos_row_floor(name: str, qty: int, listed_price: int,
                    rank: "int | None" = None) -> tuple:
    if not is_chaos_set(name):
        return 0, 0

    lots = chaos_lots_cheapest_first()
    if not lots:
        return 0, 0
    if rank is None or not (0 <= rank < len(lots)):
        lot_id, unit = lots[-1]
    else:
        lot_id, unit = lots[rank]

    units = max(int(qty or 0), pack_size(name), 1)
    return unit * units, lot_id


def chaos_shop_rows(listings: list) -> list:
    wanted = _floor_key(item_name(FAVOURITE_SLOTS.get(CHAOS_SET_SLOT, "")))
    if not wanted:
        return []
    out = []
    for row in listings:
        if getattr(row, "action", None) not in ("change", "receive"):
            continue
        name = _floor_key(item_name(_PACK_ANYWHERE.sub(" ", row.name or "")))
        if name == wanted:
            out.append(row)
    return out


def chaos_attention_needed(listings: list, trust_count: bool = True) -> str:
    rows = chaos_shop_rows(listings or [])
    if any(getattr(r, "action", None) == "receive" for r in rows):
        return "a chaos bundle has sold and is waiting to be collected"
    if trust_count:
        live = sum(1 for r in rows if getattr(r, "action", None) == "change")
        if live < CHAOS_ROWS:
            return (f"only {live} of {CHAOS_ROWS} chaos bundle(s) are on the "
                    f"board")
    return ""


def chaos_margin_now(verbose: bool = True,
                     report: "dict | None" = None) -> "int | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    cores = run_favourite_search(CHAOS_CORE_SLOT, verbose=verbose)
    if not cores:
        say("  the Chaos Core search returned nothing.")
        return None
    sets_ = run_favourite_search(CHAOS_SET_SLOT, verbose=verbose)
    if not sets_:
        say("  the Chaos Core Set search returned nothing.")
        return None
    core = _row_one(cores)
    offer = _row_one(sets_)
    if core is None or offer is None:
        say("  row 1 did not read on one of the two searches.")
        return None
    if report is not None:
        report["set_unit"] = offer.price // max(1, offer.pack)
        report["core_unit"] = core.price
        report["core_offers"] = list(cores)
    margin = chaos_margin(core.price, offer.price, offer.pack)
    if margin is None:
        say("  one of the two prices did not read.")
        return None
    say(f"  Chaos Core {core.price:,}  Set/unit "
        f"{offer.price // max(1, offer.pack):,}  margin {margin:,} "
        f"(floor {CHAOS_MARGIN_FLOOR:,})")
    return margin


def chaos_set_unit_now(verbose: bool = True) -> "int | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    sets_ = run_favourite_search(CHAOS_SET_SLOT, verbose=verbose)
    if not sets_:
        say("  the Chaos Core Set search returned nothing.")
        return None
    offer = _row_one(sets_)
    if offer is None:
        say("  row 1 of the Chaos Core Set search did not read.")
        return None
    return offer.price // max(1, offer.pack)


def chaos_rows_in(listings: list, scope: "set | None") -> list:
    rows = chaos_shop_rows(listings)
    if not scope:
        return rows
    return [r for r in rows if getattr(r, "index", None) in scope]


def chaos_boundary(scope=None) -> int:
    rows = [int(r) for r in (scope or []) if int(r) >= 1]
    return min(max(rows), SHOP_ROW_CAPACITY) if rows else SHOP_ROW_CAPACITY


def read_chaos_rows(want: "set | None",
                    timeout: float = 8.0) -> "list | None":
    top = chaos_boundary(want)

    if top > SCREEN_ROWS:
        shared = cached_range_view(top, max_age=CHAOS_VIEW_REUSE_SECONDS)
        if shared:
            return [_dc.replace(r, index=i) for i, r in shared]

    scroll_to_end(up=True, timeout=timeout, verbose=False)
    if top <= SCREEN_ROWS:
        return await_rows(timeout)
    pairs = enumerate_listings(timeout=timeout, verbose=False, stop_after=top)
    if pairs is None:
        return None
    note_range_view(max((i for i, _ in pairs), default=0), pairs)
    return [_dc.replace(r, index=i) for i, r in pairs]


def chaos_pass(timeout: float = 8.0, verbose: bool = True,
               scope: "list[int] | None" = None) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not CHAOS_ENABLED:
        return True

    if stop_requested():
        say(f"Chaos: {STOP_FILE.name} is present - not starting a pass.")
        return True

    global CHAOS_HELD_OFF_ON_MARGIN, CHAOS_HELD_OFF_MARGIN
    global CHAOS_HELD_OFF_ON_LANDING, CHAOS_HELD_OFF
    CHAOS_HELD_OFF_ON_MARGIN = False
    CHAOS_HELD_OFF_MARGIN = None
    CHAOS_HELD_OFF_ON_LANDING = False
    CHAOS_HELD_OFF = True

    try:
        edge = chaos_boundary(scope)
        want = {r for r in (scope or []) if r <= edge}
        dropped = sorted(r for r in (scope or []) if r > edge)
        if dropped:
            say(f"Chaos: rows {dropped[0]}-{dropped[-1]} are past row "
                f"{edge} and are past the shop; the shelf is kept "
                f"inside rows 1-{edge}.")

        listings = None
        if listings is None:
            listings = read_chaos_rows(want, timeout)
        if not listings:
            say("Chaos: the listings could not be read; skipping this pass.")
            return False

        rows = chaos_rows_in(listings, want)
        if want:
            say(f"Chaos is looking at rows {min(want)}-{max(want)} only.")
        sold = [r for r in rows if r.action == "receive"]
        live = [r for r in rows if r.action == "change"]
        say(f"\nCHAOS: {len(live)} live bundle(s), {len(sold)} sold and "
            f"uncollected, target {CHAOS_ROWS}.")

        if stop_requested():
            say(f"Chaos: {STOP_FILE.name} is present - stopping after the "
                f"read, before collecting or buying.")
            record("chaos.stopped", reason="stop_file", where="after_read")
            return True

        for row in sold:
            ref = RowRef.of(row, listings)
            if stop_requested():
                say(f"Chaos: {STOP_FILE.name} is present - stopping with "
                    f"{len(sold) - sold.index(row)} row(s) left to collect.")
                record("chaos.stopped", reason="stop_file", where="collecting")
                return True
            say(f"Chaos: row {row.index} has sold - collecting it now.")
            try:
                seen = (bring_into_view(ref, timeout=timeout, verbose=verbose,
                                        hint=row.index)
                        if row.index > SCREEN_ROWS else await_rows(timeout))
                here, note = (locate_row(seen, ref) if seen else (None, "no"))
                if here is None:
                    say(f"  row {row.index}: could not bring "
                        f"{row.name!r} into view to collect it ({note or 'no'
                        } view) - leaving it for the next cycle.")
                    record("chaos.collect_failed", row=row.index, why=note)
                    continue
                outcome = relist(here.index, dry_run=False, timeout=timeout,
                                 verbose=verbose, expect=ref,
                                 absolute_row=row.index)
                if outcome == FAILED:
                    say(f"  row {row.index}: the collect did not complete - "
                        f"leaving it for the next cycle.")
                    record("chaos.collect_failed", row=row.index,
                           why="relist returned FAILED")
                    continue
            except Exception as exc:
                say(f"  could not collect row {row.index}: {exc}")
                record("chaos.collect_failed", row=row.index, why=str(exc))
                continue
            record("chaos.collected", row=row.index)

        if sold:
            listings = read_chaos_rows(want, timeout) or None
        if listings is None:
            say("Chaos: the shelf could not be re-counted after collecting; "
                "stopping rather than resupplying against an unknown board.")
            record("chaos.recount_unreadable")
            return False
        live = [r for r in chaos_rows_in(listings, want)
                if r.action == "change"]
        if len(live) > CHAOS_RESTOCK_AT_OR_BELOW_ROWS:
            say(f"Chaos: {len(live)} bundle(s) on the board, above the "
                f"{CHAOS_RESTOCK_AT_OR_BELOW_ROWS} mark - not restocking yet "
                f"(target {CHAOS_ROWS}).")
            CHAOS_HELD_OFF = False
            return True

        short = CHAOS_ROWS - len(live)

        if want:
            occupied = {r.index for r in listings
                        if getattr(r, "action", None) in ("change", "receive")}
            free_here = len([i for i in want if i not in occupied])
            if short > free_here:
                say(f"Chaos: {short} short, but only {free_here} free row(s) "
                    f"inside rows {min(want)}-{max(want)} - filling what fits "
                    f"rather than listing outside the boundary.")
                short = free_here

            landing = ([i for i in range(1, max(want) + 1)
                        if i not in occupied][:short] if short > 0 else [])
            outside = [i for i in landing if i not in want]
            if outside:
                CHAOS_HELD_OFF_ON_LANDING = True
                lowest_free = outside[0]
                say(f"Chaos: {short} short, but {len(outside)} of the "
                    f"row(s) it would land in are outside this batch "
                    f"(row {lowest_free} first) - not buying. A bundle listed "
                    f"out there is never repriced again.")
                record("chaos.landing_outside", lowest_free=lowest_free,
                       floor=min(want))
                return True
        if short <= 0:
            say(f"Chaos: {len(live)} bundle(s) on the board, at target.")
            CHAOS_HELD_OFF = False
            return True
        say(f"Chaos: {short} bundle(s) short of {CHAOS_ROWS} - resupplying.")

        held_already = 0
        if chaos_stranded():
            say("Chaos: an earlier pass this run left Cores in the work tab; "
                "counting them before buying so the position is not doubled.")
            held_already = max(0, chaos_cores_held(verbose=verbose))
        if held_already:
            say(f"Chaos: {held_already} Core(s) already in hand - they count "
                f"towards the {CHAOS_BUY_QUANTITY} minimum.")
            record("chaos.already_held", held=held_already)

        if not open_purchase_tab(verbose=verbose):
            say("Chaos: could not reach the Purchase tab to price the trade.")
            return False
        margin_report: dict = {}
        margin = chaos_margin_now(verbose=verbose, report=margin_report)
        gate_offers = margin_report.get("core_offers") or None
        set_unit_price = margin_report.get("set_unit")
        if margin is None:
            say("Chaos: the margin could not be read - not buying blind.")
            record("chaos.margin_unread")
            return False
        if margin <= CHAOS_MARGIN_FLOOR:
            CHAOS_HELD_OFF_ON_MARGIN = True
            CHAOS_HELD_OFF_MARGIN = margin
            say(f"Chaos: margin {margin:,} does not clear the "
                f"{CHAOS_MARGIN_FLOOR:,} floor - not buying.")
            record("chaos.margin_low", margin=margin)
            if not held_already:
                return True
            say(f"Chaos: but {held_already} Core(s) are already paid for - "
                f"crafting and listing them rather than leaving them on the "
                f"work tab.")
            record("chaos.craft_held_on_thin_margin",
                   held=held_already, margin=margin)

        for filling_row in range(short):
            if filling_row:
                fresh_set = chaos_set_unit_now(verbose=verbose)
                if fresh_set:
                    if set_unit_price and fresh_set != set_unit_price:
                        say(f"Chaos: the Set price moved {set_unit_price:,} "
                            f"-> {fresh_set:,} since the last row.")
                        record("chaos.set_price_moved", was=set_unit_price,
                               now=fresh_set)
                    set_unit_price = fresh_set
                elif set_unit_price:
                    say(f"Chaos: the Set price did not re-read; judging this "
                        f"row against the last good {set_unit_price:,}.")
            if stop_requested():
                say("")
                say(f"Chaos: {STOP_FILE.name} is present - stopping "
                    f"before committing any Alz.")
                record("chaos.stopped", reason="stop_file")
                return True

            avoid_warlag(allowance=chaos_row_allowance(), verbose=verbose)

            if not open_purchase_tab(verbose=verbose):
                say("Chaos: could not get back to a Low-to-High Purchase tab; "
                    "stopping rather than buying off an unsorted list.")
                record("chaos.sort_unconfirmed")
                break
            core = None
            got = held_already
            held_already = 0
            paid = 0
            paid_units = 0
            lost = 0
            if BUY_HALTED:
                say(f"Chaos: buying is halted for this run ({BUY_HALT_REASON}) "
                    f"- not buying Cores.")
                record("chaos.buy_halted", why=BUY_HALT_REASON)
                return False

            for order in range(1, CHAOS_BUY_ORDERS + 1):
                if got >= CHAOS_BUY_QUANTITY:
                    break
                offers = run_favourite_search(CHAOS_CORE_SLOT,
                                              verbose=verbose)
                if not offers and not purchase_tab_open():
                    say("Chaos: the Trade window closed during the search; "
                        "reopening and trying this order once more.")
                    record("chaos.window_closed_midbuy", order=order)
                    if ensure_shop_ready(verbose=verbose) and open_purchase_tab(
                            verbose=verbose):
                        offers = run_favourite_search(CHAOS_CORE_SLOT,
                                                      verbose=verbose)
                if not offers:
                    say(f"Chaos: no Core offers left after {got} of "
                        f"{CHAOS_BUY_QUANTITY}.")
                    break
                core = _row_one(offers)
                if core is None:
                    say(f"Chaos: row 1 did not read after {got} of "
                        f"{CHAOS_BUY_QUANTITY} - stopping rather than buying "
                        f"a row nobody checked.")
                    break

                if set_unit_price:
                    here = chaos_margin(core.price, set_unit_price, 1)
                    say(f"  Chaos Core {core.price:,}  Set/unit "
                        f"{set_unit_price:,}  margin "
                        f"{here if here is not None else '?'}"
                        f"  (floor {CHAOS_MARGIN_FLOOR:,})")
                    if here is None:
                        say(f"Chaos: the margin for this row did not compute - "
                            f"stopping at {got} of {CHAOS_BUY_QUANTITY} rather "
                            f"than buying blind.")
                        record("chaos.margin_unreadable", got=got)
                        break
                    if here <= CHAOS_MARGIN_FLOOR:
                        say(f"Chaos: {here:,} does not clear the "
                            f"{CHAOS_MARGIN_FLOOR:,} floor. Stopping at {got} "
                            f"of {CHAOS_BUY_QUANTITY} rather than buying it.")
                        record("chaos.margin_gone", got=got, price=core.price)
                        break
                else:
                    say("Chaos: no Set price to judge this row against - "
                        "stopping rather than buying unguarded.")
                    record("chaos.margin_missing", got=got)
                    break

                order_size = max(1, core.available)

                order_price = core.price * order_size
                try:
                    held = get_alz(grab()) or 0
                except Exception:
                    held = 0
                if held and order_price > held:
                    fits = int(held // max(1, core.price))
                    if fits < 1:
                        say(f"Chaos: {order_price:,} Alz for {order_size} "
                            f"Core(s) is more than the {held:,} held - "
                            f"stopping at {got} of {CHAOS_BUY_QUANTITY}.")
                        record("chaos.unaffordable", want=order_size,
                               price=order_price, held=held, got=got)
                        break
                    say(f"Chaos: trimming this order from {order_size} to "
                        f"{fits} - {order_price:,} Alz is more than the "
                        f"{held:,} held.")
                    record("chaos.order_trimmed", was=order_size, now=fits,
                           held=held)
                    order_size = fits
                say(f"Chaos: order {order} - buying {order_size} x "
                    f"{core.name!r} at "
                    f"{core.price:,} = {core.price * order_size:,} Alz "
                    f"({got}/{CHAOS_BUY_QUANTITY} held so far)")
                report: dict = {}
                bought, why = buy_offer(core, want=order_size, report=report,
                                        verbose=verbose)
                if not bought:
                    say(f"Chaos: order {order} not bought - {why}")
                    record("chaos.buy_refused", why=why, got=got)
                    lost += 1
                    if lost >= CHAOS_BUY_LOST_LIMIT:
                        say(f"Chaos: {lost} orders in a row would not "
                            f"complete - stopping at {got} of "
                            f"{CHAOS_BUY_QUANTITY} rather than pressing on.")
                        break
                    if got < 1 and order >= CHAOS_BUY_ORDERS:
                        return False
                    continue
                lost = 0
                items = int(report.get("items") or order_size)
                got += items
                paid += int(report.get("spend")
                            or core.price * max(1, report.get("take", 0)))
                paid_units += items
                record("chaos.bought", items=report.get("items"),
                       price=core.price,
                       spent=core.price * report.get("take", 0), running=got)

            per_craft = craft_material_cost()
            if got and per_craft > 1 and got % per_craft:
                say(f"Chaos: {got} Core(s) is not a whole multiple of "
                    f"{per_craft}; topping up so nothing is left uncraftable "
                    f"in the work tab.")
                record("chaos.topup_start", got=got, per_craft=per_craft)
                for attempt in range(1, CHAOS_TOPUP_ORDERS + 1):
                    need = per_craft - (got % per_craft)
                    if not need or got % per_craft == 0:
                        break
                    offers = run_favourite_search(CHAOS_CORE_SLOT,
                                                  verbose=verbose)
                    if not offers:
                        say("Chaos: no Core offer to top up against.")
                        break
                    core = offers[0]
                    say(f"Chaos: top-up {attempt} - buying {need} x "
                        f"{core.name!r} at {core.price:,} = "
                        f"{core.price * need:,} Alz ({got} held)")
                    report = {}
                    bought, why = buy_offer(core, want=need, report=report,
                                            verbose=verbose)
                    if not bought:
                        say(f"Chaos: top-up {attempt} not bought - {why}")
                        record("chaos.topup_refused", why=why, got=got)
                        continue
                    items = int(report.get("items") or need)
                    got += items
                    paid += int(report.get("spend")
                                or core.price * max(1, report.get("take", 0)))
                    paid_units += items
                    record("chaos.topup_bought", items=items, running=got)
                if got % per_craft:
                    say(f"Chaos: still {got % per_craft} Core(s) over a "
                        f"multiple of {per_craft}; that many will be left in "
                        f"the work tab after the craft.")
                    record("chaos.topup_incomplete",
                           got=got, over=got % per_craft)
                else:
                    say(f"Chaos: {got} Core(s) now divides by {per_craft} "
                        f"exactly - nothing will be left over.")

            if got < 1:
                say("Chaos: nothing was bought; nothing to craft.")
                if CHAOS_HELD_OFF_ON_MARGIN:
                    return True
                return False
            say(f"Chaos: {got} Core(s) obtained"
                + ("." if got >= CHAOS_BUY_QUANTITY else
                   f" of {CHAOS_BUY_QUANTITY} - crafting what there is rather "
                   f"than leaving it in the bag."))

            leave_shop(verbose=verbose)
            time.sleep(0.5)
            if not open_craft_window(timeout=timeout, verbose=verbose):
                say("Chaos: the craft window would not open; the Cores are in "
                    "the inventory, uncrafted.")
                record("chaos.craft_window_failed")
                note_chaos_strand()
                return False
            made = craft_chaos_sets(timeout=timeout, verbose=verbose)
            press_escape()
            time.sleep(0.8)
            if made < 1:
                say("Chaos: nothing was crafted; stopping before listing.")
                note_chaos_strand()
                return False

            if not compress_stack(1, 1, verbose=verbose,
                                  tab=CHAOS_WORK_TAB):
                say("Chaos: could not compress the crafted Sets.")
                note_chaos_strand()
                return False

            ready = ensure_shop_ready(verbose=verbose)
            if not ready:
                say("Chaos: the Agent Shop did not reopen; closing anything "
                    "left over and trying once more before giving up.")
                record("chaos.reopen_retry")
                leave_shop(verbose=False)
                time.sleep(1.0)
                ready = ensure_shop_ready(verbose=verbose)
            if not ready:
                say("Chaos: the Agent Shop would not reopen to list the Sets.")
                note_chaos_strand(
                    unit_cost=(-(-paid // paid_units)) if paid_units else 0)
                return False
            origin = inventory_origin()
            if origin is not None:
                select_inventory_tab(CHAOS_WORK_TAB, origin)

            unit_cost = (-(-paid // paid_units)) if paid_units else 0
            if not unit_cost and _CHAOS_STRAND_UNIT_COST > 0:
                unit_cost = _CHAOS_STRAND_UNIT_COST
                say(f"Chaos: nothing was bought this pass; pricing the "
                    f"{made} crafted Set(s) against the "
                    f"{unit_cost:,} Alz/unit the stranded Cores cost.")
                record("chaos.resumed_cost", unit_cost=unit_cost, made=made)
            if unit_cost:
                globals()["_CHAOS_STRAND_UNIT_COST"] = int(unit_cost)
            if not unit_cost:
                say("Chaos: nothing measurable was paid, so there is no cost "
                    "floor to set - refusing to list rather than pricing the "
                    "bundle against nothing.")
                record("chaos.no_cost_basis", made=made)
                note_chaos_strand()
                return False
            cost_floor = unit_cost * made
            say(f"Chaos: cost floor {cost_floor:,} Alz "
                f"({made} x {unit_cost:,} paid"
                + (f", {paid:,} over {paid_units} Core(s)"
                   if paid_units != made else "") + ").")
            report = {}
            listed = register_item(1, 1, timeout=timeout, verbose=verbose,
                                   maximise_qty=True,
                                   cost_floor=cost_floor,
                                   expect_item=FAVOURITE_SLOTS[CHAOS_SET_SLOT],
                                   undercut=CHAOS_UNDERCUT,
                                   report=report)
            if listed:
                note_chaos_lot(unit_cost, int(report.get("price") or 0), made)
            if listed:
                note_rows_added(1)
                note_chaos_strand(False)
            record("chaos.listed", ok=bool(listed), price=report.get("price"),
                   qty=report.get("qty"), unit_cost=core.price)
            if not listed and not report.get("committed"):
                say("Chaos: the bundle did not list; marking it stranded so "
                    "the next cycle finishes it rather than re-buying.")
                note_chaos_strand()
                return False
            CHAOS_HELD_OFF = False
            say(f"Chaos: listed a bundle at {report.get('price', 0):,} Alz.")
        return True

    except Exception as exc:
        say(f"\nChaos pass did not run: {exc}")
        record("chaos.pass_failed", why=str(exc))
        try:
            if not require_empty_work_tab(verbose=False):
                note_chaos_strand()
        except Exception:
            pass
        return False
    finally:
        try:
            if craft_window_open():
                press_escape()
        except Exception:
            pass


def restock_pass(timeout: float = 8.0, verbose: bool = True,
                 scope: "list[int] | None" = None) -> None:
    if stop_requested():
        if verbose:
            print(f"  {STOP_FILE.name} is present - skipping the restock.")
        record("restock.stopped", reason="stop_file", where="entry")
        return

    def say(message: str) -> None:
        if verbose:
            print(message)

    if not restock_is_armed():
        return
    try:
            visible = await_rows(timeout)

            if not visible:
                say("The shop table did not read, so whether anything has "
                    "sold out is unknown - skipping the restock rather than "
                    "buying against a blank read.")
                record("restock.table_unread")
                return

            in_scope_now = []
            asked_range = max(scope) if scope else 0

            if scope:
                if max(scope) > EXPECTED_ROWS:
                    say(f"Rows {min(scope)}-{max(scope)} reach past the first "
                        f"screen, which shows rows 1-{EXPECTED_ROWS} only - "
                        f"reading the whole shop rather than calling Cores "
                        f"sold out from rows that were never looked at.")
                    record("restock.scope_offscreen",
                           scope=f"{min(scope)}-{max(scope)}")
                    asked_range = max(scope)
                    pairs = cached_range_view(asked_range)
                    if pairs is None:
                        pairs = enumerate_listings(timeout=timeout,
                                                   verbose=False,
                                                   stop_after=asked_range)
                        if pairs is not None:
                            note_range_view(
                                max((i for i, _ in pairs), default=0), pairs)
                    if pairs is None:
                        say("  the range could not be read - skipping the "
                            "restock this cycle rather than treating "
                            "unreadable rows as sold out.")
                        record("restock.range_unreadable", covered=asked_range)
                        return
                    visible = [_dc.replace(r, index=i) for i, r in pairs]
                    in_scope = [r for r in visible if r.index in set(scope)]
                    in_scope_now = [r for r in in_scope
                                    if getattr(r, "action", None) != "register"]
                    say(f"Restock is looking at rows {min(scope)}-"
                        f"{max(scope)} only ({len(in_scope)} of "
                        f"{len(visible)} read), not the whole shop.")
                    visible = in_scope
                else:
                    in_scope = [r for r in visible if r.index in set(scope)]
                    in_scope_now = [r for r in in_scope
                                    if getattr(r, "action", None) != "register"]
                    say(f"Restock is looking at rows {min(scope)}-{max(scope)} "
                        f"only ({len(in_scope)} of {len(visible)} read), not "
                        f"the whole shop.")
                    visible = in_scope

            missing = in_restock_priority(
                slot for slot, n in core_row_counts(visible).items()
                if n <= RESTOCK_AT_OR_BELOW_ROWS
                and slot in enabled_buying_slots())
            if not missing:
                say("Every Core that can be bought is already listed in "
                    "those rows; no shop sweep needed.")
                return

            if scope:
                say("Not in those rows: "
                    + ", ".join(FAVOURITE_SLOTS[s] for s in missing)
                    + " - restocking them without reading the rest of the "
                      "shop. Any that are listed further down will be bought "
                      "again.")
                record("restock.scoped", slots=str(missing),
                       scope=f"{min(scope)}-{max(scope)}")

                say("  scoped to rows "
                    f"{min(scope)}-{max(scope)}, so the whole-shop row count "
                    "is not needed - no sweep.")
                rows_now = cached_rows_used() or 0
                leave_for_restock(verbose=verbose)
                restock_sold_out_slots(missing, verbose=verbose,
                                       rows_used=rows_now,
                                       scope=scope,
                                       rows_in_scope_used=len(in_scope_now),
                                       chaos_rows_in_scope=len(
                                           chaos_shop_rows(in_scope_now)))
                return
            say("Not on the first screen: "
                + ", ".join(FAVOURITE_SLOTS[s] for s in missing)
                + " - sweeping the shop to see if they are further down.")
            if BUY_NO_SWEEP:
                say("  --buy-no-sweep: treating them as sold out without "
                    "reading the rest of the shop. A Core listed below the "
                    "first screen will be bought again.")
                record("restock.no_sweep", slots=str(missing))
                rows_now = cached_rows_used()
                if rows_now is None:
                    rows_now = shop_rows_used(verbose=False)
                leave_for_restock(verbose=verbose)
                restock_sold_out_slots(missing, verbose=verbose,
                                       rows_used=rows_now,
                                       scope=scope,
                                       rows_in_scope_used=len(in_scope_now),
                                       chaos_rows_in_scope=len(
                                           chaos_shop_rows(in_scope_now)))
                return
            remembered = cached_unlisted(missing)
            if remembered is not None:
                say("Using the last shop sweep rather than repeating it "
                    f"({len(remembered)} Core(s) still unlisted).")
                rows_now = cached_rows_used()
                if rows_now is None:
                    rows_now = shop_rows_used(verbose=False)
                else:
                    say(f"  ...and its row count too ({rows_now}/30 in use), "
                        f"so the table is not walked at all.")
                leave_for_restock(verbose=verbose)
                restock_sold_out_slots(remembered, verbose=verbose,
                                       scope=scope,
                                       rows_in_scope_used=len(in_scope_now),
                                       chaos_rows_in_scope=len(
                                           chaos_shop_rows(in_scope_now)),
                                       rows_used=rows_now)
                return
            sweep_started = time.monotonic()
            want_rows = max(scope) if scope else asked_range
            everything = None
            if want_rows:
                pairs = cached_range_view(want_rows)
                if pairs is not None:
                    say(f"  reusing this cycle's walk of rows 1-{want_rows} "
                        f"rather than reading the table again.")
                    everything = rows_as_read(pairs)
            if everything is None:
                stop_at = want_rows or None
                if SHOP.enforce and not SHOP.ready:
                    say("  row model: walking the WHOLE shop once to seed it; "
                        "later cycles answer from the model instead.")
                    stop_at = None
                walked = shop_listing_pairs(timeout=timeout, verbose=verbose,
                                            stop_after=stop_at)
                if walked:
                    everything = rows_as_read(walked)
                    if want_rows:
                        note_range_view(max(i for i, _ in walked), walked)
                    covered = max(i for i, _ in walked)
                    if (SHOP.enforce and not SHOP.ready
                            and covered >= SHOP_ROW_CAPACITY):
                        SHOP.adopt(walked)
                        say(SHOP.describe())
                    elif SHOP.enforce and not SHOP.ready:
                        record("shopmodel.not_seeded", covered=covered,
                               capacity=SHOP_ROW_CAPACITY)
            say(f"  the shop sweep took "
                f"{time.monotonic() - sweep_started:.0f}s.")
            if everything is None:
                say("\nRestock skipped: the shop could not be enumerated, and "
                    "a partial read is what makes a stocked item look absent.")
            else:
                note_unlisted(slots_needing_restock(everything))
                note_rows_used(rows_in_use(everything))
                leave_for_restock(verbose=verbose)
                restock_sold_out(everything, verbose=verbose)
    except Exception as exc:
        say(f"\nRestock pass did not run: {exc}")
    finally:
        try:
            if vendor_shop_open():
                say("  closing the vendor Shop before handing back.")
                close_npc_shop(verbose=verbose)
        except Exception:
            pass


def run_sequence(actions: list[str], dry_run: bool = False, verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not actions:
        say("No actions to run - that is a caller error, not a completed "
            "cycle, so it is reported as a failure rather than resetting the "
            "failure breaker.")
        return False

    for position, spec in enumerate(actions, start=1):
        parts = spec.split()
        if not parts:
            say(f"[{position}/{len(actions)}] empty action - stopping.")
            return False

        verb, args = parts[0].casefold(), parts[1:]
        say(f"\n[{position}/{len(actions)}] {spec}")

        try:
            if verb == "cancel" and len(args) == 1:
                ok = cancel_item(int(args[0]), dry_run=dry_run, verbose=verbose)
            elif verb == "register" and len(args) in (2, 3):
                forced = int(args[2]) if len(args) == 3 else None
                ok = register_item(int(args[0]), int(args[1]),
                                   dry_run=dry_run, verbose=verbose,
                                   force_price=forced)
            elif verb == "relist" and len(args) in (1, 3):
                slot = (int(args[1]), int(args[2])) if len(args) == 3 else (None, None)
                ok = relist(int(args[0]), *slot,
                            dry_run=dry_run, verbose=verbose) != FAILED
            elif verb in ("relist-rows", "relist_rows") and args:
                ok = relist_rows(parse_row_spec(args), dry_run=dry_run,
                                 verbose=verbose,
                                 all_rows=wants_all_rows(args))
            elif verb == "clear" and not args:
                ok = True if dry_run else clear_shop_slot(verbose=verbose)
            else:
                say(f"Unknown action {spec!r}. Use 'cancel ROW', "
                    "'register ROW COL', 'relist ROW [R C]', "
                    "'relist-rows 1-10', or 'clear'.")
                return False
        except ValueError:
            say(f"Action {spec!r} has a non-numeric argument.")
            return False
        except Aborted as exc:
            say(f"Stopped: {exc}")
            return False

        if not ok:
            say(f"Action {position} failed - stopping; "
                f"{len(actions) - position} action(s) not attempted.")
            return False

        if not dry_run and not wait_for_table():
            say("The table did not finish refreshing - stopping.")
            return False

    say(f"\nAll {len(actions)} action(s) completed.")
    return True


_SERVER_CLOCK_TEXT = re.compile(r"^([01]\d|2[0-3])[:.]([0-5]\d)")

_SERVER_CLOCK_SYNC: "tuple[float, _dt.datetime] | None" = None


def read_server_clock(source: "Image.Image | None" = None,
                      verbose: bool = False) -> "_dt.time | None":
    shot = source if source is not None else grab()
    left, top, right, bottom = SERVER_CLOCK_REGION
    if right <= left or bottom <= top:
        if verbose:
            print(f"  the server clock region {SERVER_CLOCK_REGION} is not a "
                  f"valid box on this screen - the war schedule cannot run.")
        record("warlag.clock_region_invalid", region=str(SERVER_CLOCK_REGION))
        return None
    words = [w for w in find_words(shot, SERVER_CLOCK_REGION, 20)
             if w.conf >= 40]
    match = None
    for word in words:
        match = _SERVER_CLOCK_TEXT.match(word.text.strip())
        if match is not None:
            break
    text = " ".join(w.text for w in words)
    if match is None:
        if verbose:
            print(f"  the server clock did not read ({text!r}).")
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        if verbose:
            print(f"  the server clock read {hour}:{minute:02d}, which is not "
                  f"a time - ignoring it.")
        record("warlag.clock_absurd", text=text, hour=hour, minute=minute)
        return None
    return _dt.time(hour, minute)


def sync_server_clock(verbose: bool = False) -> bool:
    global _SERVER_CLOCK_SYNC
    reading = read_server_clock(verbose=verbose)
    if reading is None:
        return False
    stamped = SERVER_CLOCK_EPOCH + _dt.timedelta(
        hours=reading.hour, minutes=reading.minute,
        seconds=SERVER_CLOCK_UNCERTAINTY)
    if _SERVER_CLOCK_SYNC is None:
        time.sleep(SERVER_CLOCK_CONFIRM_PAUSE)
        second = read_server_clock(grab(), verbose=False)
        if second is None:
            if verbose:
                print("  the server clock did not read a second time; not "
                      "anchoring on a single reading.")
            record("warlag.clock_unconfirmed")
            return False
        gap = abs((second.hour * 60 + second.minute)
                  - (reading.hour * 60 + reading.minute))
        if gap > 1:
            if verbose:
                print(f"  two readings disagree "
                      f"({reading.hour:02d}:{reading.minute:02d} then "
                      f"{second.hour:02d}:{second.minute:02d}) - not "
                      f"anchoring on either.")
            record("warlag.clock_disagreed",
                   first=f"{reading.hour:02d}:{reading.minute:02d}",
                   second=f"{second.hour:02d}:{second.minute:02d}")
            return False

    previous = server_now(resync=False, verbose=False)
    if previous is not None:
        drift = abs((stamped - previous).total_seconds())
        if drift > SERVER_CLOCK_MAX_DRIFT:
            if verbose:
                print(f"  the server clock read "
                      f"{reading.hour:02d}:{reading.minute:02d}, which is "
                      f"{drift / 60:.1f} min from the running clock "
                      f"({previous:%H:%M:%S}) - keeping the old anchor.")
            record("warlag.clock_rejected", read=stamped.strftime("%H:%M"),
                   running=previous.strftime("%H:%M:%S"), drift=round(drift))
            return False

    _SERVER_CLOCK_SYNC = (time.monotonic(), stamped)
    if verbose:
        print(f"  server clock reads {reading.hour:02d}:{reading.minute:02d} "
              f"(read from the game, not from this machine).")
    record("warlag.clock_synced", server=stamped.strftime("%H:%M"))
    return True


def server_now(resync: bool = True,
               verbose: bool = False) -> "_dt.datetime | None":
    global _SERVER_CLOCK_SYNC
    stale = (_SERVER_CLOCK_SYNC is None
             or time.monotonic() - _SERVER_CLOCK_SYNC[0] > SERVER_CLOCK_RESYNC)
    if stale and resync:
        sync_server_clock(verbose=verbose)
    if _SERVER_CLOCK_SYNC is None:
        return None
    at, stamped = _SERVER_CLOCK_SYNC
    return stamped + _dt.timedelta(seconds=time.monotonic() - at)


def war_quiet_window(after: "_dt.datetime") -> "tuple[_dt.datetime, _dt.datetime]":
    best: "tuple[_dt.datetime, _dt.datetime] | None" = None
    for day in (-1, 0, 1):
        midnight = (after + _dt.timedelta(days=day)).replace(
            hour=0, minute=0, second=0, microsecond=0)
        for hour in WAR_START_HOURS:
            ends = midnight + _dt.timedelta(hours=hour, minutes=WAR_MINUTES)
            start = ends - _dt.timedelta(seconds=WAR_QUIET_BEFORE_END)
            end = start + _dt.timedelta(seconds=WAR_QUIET_SECONDS
                                        + SERVER_CLOCK_UNCERTAINTY)
            if end <= after:
                continue
            if best is None or start < best[0]:
                best = (start, end)
    assert best is not None
    return best


def avoid_warlag(allowance: float = 0.0, verbose: bool = True,
                 dry_run: bool = False) -> float:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if dry_run:
        return 0.0

    now = server_now(verbose=verbose)
    if now is None:
        say("  the server clock has never read, so the war schedule cannot be "
            "followed this run.")
        return 0.0

    start, end = war_quiet_window(now)
    if now < start and (start - now).total_seconds() > allowance:
        return 0.0

    wait = (end - now).total_seconds()
    if wait <= 0:
        return 0.0

    reason = ("a war ends in "
              f"{(start + _dt.timedelta(seconds=WAR_QUIET_BEFORE_END) - now).total_seconds() / 60:.1f} min"
              if now < start else "a war has just ended")
    say(f"\nWAR LAG: {reason} (server {now:%H:%M:%S}). Going to the default "
        f"state and waiting {wait / 60:.1f} min, until server "
        f"{end:%H:%M:%S}.")
    record("warlag.pausing", server=now.strftime("%H:%M:%S"),
           until=end.strftime("%H:%M:%S"), seconds=round(wait, 1),
           allowance=allowance)

    try:
        if not leave_shop(verbose=verbose):
            say("  the Agent Shop would not close; waiting anyway, but the "
                "next cycle may have to rebuild from the NPC.")
            record("warlag.shop_would_not_close")
    except Exception as exc:
        say(f"  could not close the shop before waiting ({exc}); waiting "
            "anyway.")

    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(min(5.0, deadline - time.monotonic()))
    say(f"WAR LAG: done waiting; resuming where it left off.")
    record("warlag.resumed", seconds=round(wait, 1))
    return wait


def leave_shop(verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    note_shop_closed()
    try:
        if dialog_present():
            say("Closing the dialog left on screen...")
            close_any_dialog()
        dismiss_purchase_dialog(verbose=verbose)
        for _ in range(ESCAPE_ATTEMPTS):
            if not trade_window_open():
                say("Agent Shop closed; the game is back to its default state.")
                return True
            press_escape()
        if trade_window_open():
            say("Note: the Trade window would not close with Escape - close it "
                "by hand before the next run.")
            return False
        say("Agent Shop closed; the game is back to its default state.")
        return True
    except Exception as exc:
        say(f"Note: could not tidy up the game window ({exc}).")
        return False


def prepare_for_actions(verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    if not focus_game():
        say("Could not bring Cabal to the foreground.")
        return False

    release_modifiers()
    park_cursor()

    for attempt in range(ESCAPE_ATTEMPTS):
        if not dialog_present():
            break
        say(f"Dialog still open - pressing Escape ({attempt + 1}).")
        press_escape()
    else:
        if not close_any_dialog():
            say("Could not close a dialog left open on screen.")
            return False

    if not open_trade_window(verbose=verbose):
        say("Could not open the Trade window on the Register tab.")
        return False

    if not calibrate(verbose=verbose, save=False):
        say("Could not measure the layout for this cycle - not acting on "
            "coordinates that have not been checked.")
        return False

    if not clear_shop_slot(verbose=verbose):
        say("Could not clear the shop slot.")
        return False

    return True


def run_loop(
    actions: list[str],
    minutes: float,
    every: float,
    dry_run: bool = False,
    verbose: bool = True,
) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    interval = every * 60.0
    end = time.monotonic() + minutes * 60.0
    cycle = succeeded = failures = consecutive = idle = 0
    stopped = False
    finished = False

    if every:
        cadence = (f"every {every:g} min for {minutes:g} min "
                   f"(about {max(1, int(minutes / every))} cycles)")
    else:
        cadence = f"back to back for {minutes:g} min"
    export_live_config(verbose=verbose)

    say(f"Looping {actions} {cadence}. A failed cycle is retried on the next "
        f"tick. Ctrl+C to quit.")

    if keep_awake(True):
        say("Holding off display/system sleep for the duration.")
    else:
        say("WARNING: could not inhibit sleep; the run may be cut short by it.")

    try:
        while time.monotonic() < end:
            if stop_requested():
                record("loop.stopped", reason="stop_file", cycle=cycle)
                say("")
                say(f"{STOP_FILE.name} is present - stopping cleanly "
                    f"between cycles.")
                stopped = True
                finished = True
                break
            if BUY_HALTED:
                record("loop.stopped", reason="buy_halted", cycle=cycle,
                       why=BUY_HALT_REASON)
                say("")
                say(f"BUYING IS HALTED: {BUY_HALT_REASON}")
                say("Nothing further can be bought, so the run is stopping "
                    "rather than cycling on a shelf it cannot refill.")
                stopped = True
                break

            apply_live_config(verbose=verbose)

            cycle += 1
            started = time.monotonic()
            if OCR_PROFILE and cycle > 1:
                _rep = ocr_profile_report()
                if _rep:
                    say(_rep)
                    _c = ocr_cache_stats()
                    _served = _c.get("hits", 0)
                    _asked = _served + _c.get("misses", 0)
                    if _asked:
                        say(f"  cache: {_served} of {_asked} reads served "
                            f"without a launch "
                            f"({100.0 * _served / _asked:.0f}%)")

            if cycle > 1:
                walks = walks_this_cycle()
                verdict = ("as designed" if walks <= 1
                           else "MORE THAN ONE - a pass could not "
                                "reuse the shared read")
                say(f"  table walks last cycle: {walks} ({verdict})")
            say(f"\n===== cycle {cycle} at {datetime.now():%H:%M:%S} =====")

            avoid_warlag(allowance=WAR_CYCLE_ALLOWANCE, verbose=verbose)
            record("cycle.start", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)
            reset_walk_count()

            if session_locked():
                say("The workstation is locked - screen capture and input are "
                    "unavailable. Terminating the loop.")
                stopped = True
                break

            try:
                if dry_run or prepare_for_actions(verbose=verbose):
                    if run_sequence(actions, dry_run=dry_run, verbose=verbose):
                        succeeded += 1
                        consecutive = 0
                    else:
                        failures += 1
                        consecutive += 1
                        say(f"\nCycle {cycle} failed - will retry next cycle.")
                else:
                    failures += 1
                    consecutive += 1
                    say(f"\nCycle {cycle}: could not get the game ready - "
                        "will retry next cycle.")
            except ShopEmpty as exc:
                record("loop.stopped", reason="sold_out", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                say(f"\nSOLD OUT: {exc}")
                succeeded += 1
                consecutive = 0
                stopped = True
                finished = True
                leave_shop(verbose=verbose)
                break
            except ShopIdle as exc:
                idle += 1
                record("cycle.idle", cycle=cycle, detail=str(exc), idle=idle)
                say("")
                say(f"NOTHING TO DO: {exc}.")
                say(f"  ({idle} idle cycle(s) so far; the loop keeps checking "
                    f"and resumes the moment there is work.)")
            except FatalAbort as exc:
                record("loop.stopped", reason="fatal", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                say(f"\nFATAL: {exc}")
                say("Terminating the loop; nothing further will be attempted.")
                failures += 1
                stopped = True
                leave_shop(verbose=verbose)
                break
            except PermissionError as exc:
                record("loop.stopped", reason="permission", detail=str(exc),
                       cycle=cycle, consecutive=consecutive)
                say(f"\nInput was refused: {exc}")
                say("Terminating the loop; nothing further can be clicked.")
                failures += 1
                stopped = True
                break
            except Exception as exc:
                record("cycle.exception", cycle=cycle, exc=type(exc).__name__,
                       detail=str(exc), traceback=traceback.format_exc(),
                       consecutive=consecutive)
                say(traceback.format_exc())
                failures += 1
                consecutive += 1
                say(f"\nCycle {cycle} raised {type(exc).__name__}: {exc}")
                if session_locked():
                    record("loop.stopped", reason="locked", cycle=cycle)
                    say("The workstation is locked - terminating the loop.")
                    stopped = True
                    break
                say("Will retry next cycle.")

            record("cycle.end", cycle=cycle, consecutive=consecutive,
                   succeeded=succeeded, failures=failures)

            if consecutive and game_disconnected():
                record("loop.stopped", reason="disconnected", cycle=cycle)
                say("\nThe GAME HAS DISCONNECTED - the client is showing "
                    "'You have been disconnected from the server'.")
                say("Nothing this script does can reach the server until you "
                    "click OK and log back in, so it is stopping here rather "
                    "than retrying.")
                say(f"Anything in inventory tab {WORK_TAB} is safe; the next "
                    "run will pick it up.")
                stopped = True
                break

            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                record("loop.stopped", reason="consecutive_failures",
                       cycle=cycle, consecutive=consecutive)
                say(f"\n{consecutive} cycles have failed in a row - stopping "
                    "rather than repeating it for the rest of the run. Check "
                    f"the shop and inventory tab {WORK_TAB} by hand.")
                stopped = True
                break

            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            wait = min(max(0.0, interval - (time.monotonic() - started)), remaining)
            if wait > 0:
                say(f"Next cycle in {wait / 60:.1f} min "
                    f"({remaining / 60:.1f} min left overall).")
                time.sleep(wait)
            elif time.monotonic() - started < MIN_CYCLE_SECONDS:
                time.sleep(min(MIN_CYCLE_SECONDS - (time.monotonic() - started),
                               remaining))
    except KeyboardInterrupt:
        record("loop.stopped", reason="interrupt")
        say("\nInterrupted - stopping the loop.")
        global _INTERRUPTED
        _INTERRUPTED = True
        stopped = True
    finally:
        keep_awake(False)
        try:
            leave_shop(verbose=verbose)
        except Exception as exc:
            say(f"Note: could not tidy up the game window ({exc}).")
        except BaseException:
            say("Interrupted while closing the Agent Shop - it may still be "
                "open. Close it by hand, or the next run cannot see the NPC.")
            raise

    say(f"\nDone: {cycle} cycle(s) run, {succeeded} succeeded, {failures} failed"
        + (f", {idle} idle (nothing to do)" if idle else "")
        + (f"; stopped early at cycle {cycle}." if stopped else "."))
    return (finished
            or (succeeded > 0 and not stopped)
            or (idle > 0 and not stopped))


_TRADE_FRAME_GEOMETRY = {
    "TRADE_REGION": "box", "REGISTER_PANEL": "box", "PRICE_ROWS": "box",
    "PRICE_FIELD": "box", "QTY_FIELD": "box", "NET_SALES_ROWS": "box",
    "SHOP_SLOT_BOX": "box",
    "SHOP_SLOT": "point", "QTY_INPUT": "point", "PARK_POINT": "point",
    "PRICE_TOP_Y": "y", "PRICE_BOTTOM_Y": "y",
    "PANEL_RADIO_X": "x", "PRICE_TEXT_MAX_X": "x",
    "NAME_COLUMN": "xpair",
    "DIALOG_BUTTON_MIN_X": "x",
    "PRICE_ROW_Y_TOL": "len",
    "POPUP_REGION": "box",
    "NPC_SEARCH_REGION": "box",
    "SHOP_WINDOW_TITLE": "box",
    "CONVERT_TIP_REGION": "box",
    "CONVERT_DIALOG_REGION": "box",
    "CONVERT_DLG_ITEM": "box",
    "CONVERT_DLG_PRICE": "box",
    "CONVERT_DLG_QTY_VALUE": "box",
    "CONVERT_DLG_QTY_MAX": "box",
    "CONVERT_DIALOG_BUTTONS": "box",
    "VENDOR_TAB_REGION": "box",
    "PURCHASE_SORT_REGION": "box",
    "SERVER_CLOCK_REGION": "box",
    "CRAFT_RECIPES": "recipe_points",
    "CRAFT_REPEAT_POINT": "point",
    "CRAFT_REQUEST_ALL": "point",
    "CRAFT_COMPLETE_ALL": "point",
    "CRAFT_MATERIAL_REGION": "box",
    "CRAFT_WINDOW_REGION": "box",
    "PURCHASE_SORT_BUTTON": "point",
    "PURCHASE_SORT_OPTIONS": "box",
    "CONVERT_COLS": "xs",
    "CONVERT_ROWS": "ys",
    "VENDOR_TAB_BAND": "ypair",
    "FAVOURITE_FIRST": "point",
    "FAVOURITE_PITCH": "len",
    "PURCHASE_ROW_TOP": "y",
    "PURCHASE_ROW_PITCH": "len",
    "PURCHASE_BUY_X": "x",
    "PURCHASE_NAME_MAX_X": "x",
    "PURCHASE_PRICE_X": "xpair",
    "PURCHASE_ROW_BAND_X": "xpair",
    "PURCHASE_ROW_HALF": "len",
    "PURCHASE_ROW_SELECT_X": "x",
    "VENDOR_TAB_HALF_W": "len",
    "PURCHASE_DIALOG_REGION": "box",
    "PURCHASE_DIALOG_BUTTONS_Y": "y",
    "PURCHASE_DLG_ITEM": "box",
    "PURCHASE_DLG_QTY_VALUE": "box",
    "PURCHASE_DLG_QTY_MAX": "box",
    "PURCHASE_DLG_PRICE": "box",
    "PURCHASE_DIALOG_BUTTONS": "box",
    "DISCONNECT_REGION": "box",
    "TRADE_WINDOW_SEARCH": "box",
    "TRADE_TOP_BAND": "box",
    "TABLE_HEAD_BAND": "box",
    "SCROLL_POINT": "point",
}

REF_CLIENT = (0, 23, 2560, 1392)
_CLIENT_FRAME_GEOMETRY = ("ALZ_REGION", "INVENTORY_TITLE_REGION")

_INVENTORY_FRAME_GEOMETRY = {
    "SLOT_PITCH": "lenpair", "SLOT_ONE_OFFSET": "lenpair",
    "TAB_ONE_OFFSET": "lenpair", "ALZ_TO_TITLE": "lenpair",
    "TAB_PITCH": "len", "SLOT_INSET": "len",
    "TAB_SAMPLE_HALF_W": "len", "TAB_SAMPLE_BAND": "lenpair",
}

_REFERENCE_GEOMETRY: dict[str, object] = {}


def _capture_reference_geometry() -> None:
    ox, oy = REF_TRADE_ORIGIN
    for name, kind in _TRADE_FRAME_GEOMETRY.items():
        value = globals()[name]
        if kind == "box":
            value = (value[0] - ox, value[1] - oy, value[2] - ox, value[3] - oy)
        elif kind == "point":
            value = (value[0] - ox, value[1] - oy)
        elif kind == "recipe_points":
            value = {k: ((v[0][0] - ox, v[0][1] - oy),
                         (v[1][0] - ox, v[1][1] - oy), v[2])
                     for k, v in value.items()}
        elif kind == "x":
            value = value - ox
        elif kind == "y":
            value = value - oy
        elif kind == "xpair":
            value = (value[0] - ox, value[1] - ox)
        elif kind == "xs":
            value = tuple(v - ox for v in value)
        elif kind == "ys":
            value = tuple(v - oy for v in value)
        elif kind == "ypair":
            value = (value[0] - oy, value[1] - oy)
        elif kind == "boxes":
            value = tuple((b[0] - ox, b[1] - oy, b[2] - ox, b[3] - oy)
                          for b in value)
        _REFERENCE_GEOMETRY[name] = value

    for name in _INVENTORY_FRAME_GEOMETRY:
        _REFERENCE_GEOMETRY[name] = globals()[name]

    cl, ct, cr, _cb = REF_CLIENT
    for name in _CLIENT_FRAME_GEOMETRY:
        left, top, right, bottom = globals()[name]
        _REFERENCE_GEOMETRY[name] = (cr - left, top - ct, cr - right, bottom - ct)


def apply_layout(layout: "Layout") -> None:
    global _CALIBRATED
    _CALIBRATED = True

    global LAYOUT
    LAYOUT = layout
    if not _REFERENCE_GEOMETRY:
        _capture_reference_geometry()

    for name, kind in _TRADE_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "box":
            value = layout.box(ref)
        elif kind == "point":
            value = layout.point(ref)
        elif kind == "recipe_points":
            value = {k: (layout.point(v[0]), layout.point(v[1]), v[2])
                     for k, v in ref.items()}
        elif kind == "x":
            value = layout.x(ref)
        elif kind == "y":
            value = layout.y(ref)
        elif kind == "xpair":
            value = (layout.x(ref[0]), layout.x(ref[1]))
        elif kind == "xs":
            value = tuple(layout.x(v) for v in ref)
        elif kind == "ys":
            value = tuple(layout.y(v) for v in ref)
        elif kind == "ypair":
            value = (layout.y(ref[0]), layout.y(ref[1]))
        elif kind == "boxes":
            value = tuple(_clamp_box(layout.box(b), layout.screen) for b in ref)
        else:
            value = layout.length(ref)
        if kind == "box":
            value = _clamp_box(value, layout.screen)
        globals()[name] = value

    for name, kind in _INVENTORY_FRAME_GEOMETRY.items():
        ref = _REFERENCE_GEOMETRY[name]
        if kind == "lenpair":
            value = (ref[0] * layout.scale, ref[1] * layout.scale)
            if all(float(v).is_integer() for v in ref):
                value = (int(round(value[0])), int(round(value[1])))
        else:
            value = ref * layout.scale
            if float(ref).is_integer():
                value = int(round(value))
        globals()[name] = value

    client = layout.client or (0, 0, *layout.screen)
    cl, ct, cr, cb = client
    for name in _CLIENT_FRAME_GEOMETRY:
        left_in, top_in, right_in, bottom_in = _REFERENCE_GEOMETRY[name]
        globals()[name] = _clamp_box(
            (cr - layout.length(left_in), ct + layout.length(top_in),
             cr - layout.length(right_in), ct + layout.length(bottom_in)),
            layout.screen)

    globals()["NPC_BODY_OFFSET"] = (
        int(round(_REFERENCE_NPC_BODY_OFFSET[0] * layout.scale)),
        int(round(_REFERENCE_NPC_BODY_OFFSET[1] * layout.scale)))
    globals()["NPC_CLICK_OFFSETS"] = _npc_click_offsets()

    globals()["NPC_EXCLUDE_ZONES"] = tuple(
        (int(round(cl + fl * (cr - cl))), int(round(ct + ft * (cb - ct))),
         int(round(cl + fr * (cr - cl))), int(round(ct + fb * (cb - ct))))
        for fl, ft, fr, fb in NPC_EXCLUDE_FRACTIONS)


_REFERENCE_NPC_BODY_OFFSET = NPC_BODY_OFFSET

def client_rect() -> tuple[int, int, int, int] | None:
    hwnd = find_game_window()
    if not hwnd:
        return None
    try:
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.GetClientRect(ctypes.c_void_p(hwnd),
                                                  ctypes.byref(rect)):
            return None
        point = ctypes.wintypes.POINT(rect.left, rect.top)
        if not ctypes.windll.user32.ClientToScreen(ctypes.c_void_p(hwnd),
                                                   ctypes.byref(point)):
            return None
        return (point.x, point.y,
                point.x + (rect.right - rect.left),
                point.y + (rect.bottom - rect.top))
    except Exception:
        return None


def _anchor_centre(phrase: str, words: list, lines: list):
    if " " in phrase:
        target = _normalise(phrase)
        for line in lines:
            if target in _normalise("".join(w.text for w in line)):
                window = _minimal_window(line, (target,))
                if window:
                    return _span_centre(window)
        return None
    needle = phrase.casefold()
    hits = sorted((w for w in words if needle in w.text.casefold()),
                  key=lambda w: w.top)

    if len(hits) > 1:
        return None
    if hits:
        return hits[0].centre
    if len(needle) >= 5:
        for w in sorted(words, key=lambda x: x.top):
            text = w.text.casefold().strip()
            if w.conf < NEAR_ANCHOR_MIN_CONF or not text:
                continue
            lost_first = text == needle[1:]
            lost_last = text == needle[:-1]
            if not (lost_first or lost_last):
                continue
            glyph = (w.right - w.left) / max(len(text), 1)
            cx, cy = w.centre
            return (int(round(cx - glyph / 2 if lost_first else cx + glyph / 2)),
                    cy)
    return None


def measure_layout(image: Image.Image | None = None,
                   verbose: bool = True) -> "Layout | None":
    def say(message: str) -> None:
        if verbose:
            print(message)

    shot = image if image is not None else grab()
    screen = shot.size
    client = client_rect()
    search = (0, 0, screen[0], screen[1])

    def collect(scale_override=None):
        got = find_words(shot, search, 40.0, scale=scale_override) \
            if scale_override else find_words(shot, search, 40.0)
        return got, _text_lines(got)

    words, lines = collect()
    attempts = [(words, lines)]
    hits = sum(1 for p, _ in REF_ANCHORS
               if _anchor_centre(p, words, lines) is not None)

    def _y_spread(ws, ls) -> float:
        ys = []
        for phrase, ref in REF_ANCHORS_ALL:
            if _anchor_centre(phrase, ws, ls) is not None:
                ys.append(ref[1])
        return (max(ys) - min(ys)) if len(ys) >= 2 else 0.0

    need_more = (hits < MIN_ANCHORS_AFTER_DROP
                 or _y_spread(words, lines) < MIN_ANCHOR_SPREAD)
    if need_more:
        for bigger in (3, 4, 5):
            words2, lines2 = collect(bigger)
            attempts.append((words2, lines2))
            got = sum(1 for p, _ in REF_ANCHORS
                      if _anchor_centre(p, words2, lines2) is not None)
            if got >= len(REF_ANCHORS) and _y_spread(words2, lines2) >= MIN_ANCHOR_SPREAD:
                break

    found: list[tuple[str, tuple[int, int], tuple[int, int]]] = []
    for phrase, ref_point in REF_ANCHORS_ALL:
        centre = None
        used = 0
        for n, (ws, ls) in enumerate(attempts):
            centre = _anchor_centre(phrase, ws, ls)
            if centre is not None:
                used = n
                break
        if centre is not None:
            found.append((phrase, centre, ref_point))
            say(f"  anchor {phrase!r:16} at {centre}  (reference {ref_point})"
                + ("" if used == 0 else "  [found on a larger OCR pass]"))
        else:
            say(f"  anchor {phrase!r:16} not found")

    if len(found) < 3:
        say(f"\nCalibration could not measure the Trade window.")
        say(f"  display captured   {screen[0]}x{screen[1]}  (primary display only)")
        if client:
            say(f"  game client area   ({client[0]},{client[1]})-({client[2]},{client[3]})")
            if (client[0] < 0 or client[1] < 0
                    or client[2] > screen[0] or client[3] > screen[1]):
                say("  >> The game window is NOT on the display being captured.")
                say("     Screenshots only ever cover the primary display, so the")
                say("     Trade window was never in the frame and nothing here can")
                say("     be measured. Move Cabal to the primary display.")
                return None
        say(f"  words read on screen {len(words)}")
        if not words:
            say("  >> OCR returned nothing at all, on the whole screen. That is")
            say("     Tesseract failing, not the game. Nothing about the window")
            say("     can be concluded from this frame -- check the messages")
            say("     above and that Tesseract is installed.")
            return None
        say(f"  anchors found      {len(found)} of {len(REF_ANCHORS_ALL)} "
            f"({len(REF_ANCHORS)} required, {len(REF_ANCHORS_EXTRA)} "
            f"Register-tab only)")
        for phrase, centre, ref in found:
            say(f"    {phrase!r:12} at {centre}   reference {ref}")
        missing = [p for p, _ in REF_ANCHORS if p not in {f[0] for f in found}]
        say(f"  anchors missing    {', '.join(repr(p) for p in missing)}")
        low = [p for p in missing
               if dict(REF_ANCHORS)[p][1] > REF_SCREEN[1] // 2]
        if low and len(low) == len(missing):
            say("  >> Every missing anchor sits along the window's lower half.")
            say("     The bottom of the Trade window is off-screen or covered.")
        say("\n  Three anchors are the minimum: with two, an error along the line")
        say("  between them is indistinguishable from a different scale, so the")
        say("  result cannot be checked.")
        say("\n  Re-running --calibrate will read the same screen and fail the")
        say("  same way. Change one of these first:")
        say("    - move the Cabal window fully onto the primary display;")
        say("    - close any overlay covering the Trade window;")
        say("    - drag the Trade window so all four corners are visible.")
        return None

    def fit(anchors):
        refs = [r for _, _, r in anchors]
        obs = [m for _, m, _ in anchors]
        span = max(math.hypot(a[0] - b[0], a[1] - b[1])
                   for a in refs for b in refs)
        if span < MIN_ANCHOR_BASELINE:
            return None, (f"the anchors found span only {span:.0f}px in the "
                          "reference frame - too close together to measure a "
                          "scale from.")

        x_spread = max(r[0] for r in refs) - min(r[0] for r in refs)
        y_spread = max(r[1] for r in refs) - min(r[1] for r in refs)
        if min(x_spread, y_spread) < MIN_ANCHOR_SPREAD:
            return None, (f"the anchors cover {x_spread:.0f}px horizontally "
                          f"and {y_spread:.0f}px vertically; at least "
                          f"{MIN_ANCHOR_SPREAD}px on BOTH axes is needed, or "
                          "the fit is extrapolated off the line they sit on.")

        rx = sum(r[0] for r in refs) / len(refs)
        ry = sum(r[1] for r in refs) / len(refs)
        mx = sum(m[0] for m in obs) / len(obs)
        my = sum(m[1] for m in obs) / len(obs)
        numerator = sum((r[0] - rx) * (m[0] - mx) + (r[1] - ry) * (m[1] - my)
                        for r, m in zip(refs, obs))
        denominator = sum((r[0] - rx) ** 2 + (r[1] - ry) ** 2 for r in refs)
        if denominator <= 0:
            return None, "the anchors found are all in the same place."
        s = numerator / denominator
        if not SCALE_LIMITS[0] <= s <= SCALE_LIMITS[1]:
            return None, (f"measured scale {s:.3f} is outside the plausible "
                          f"range {SCALE_LIMITS} - the anchors were misread.")

        ox_, oy_ = mx - s * rx, my - s * ry
        res = [(name, math.hypot(m[0] - (ox_ + s * r[0]),
                                 m[1] - (oy_ + s * r[1])))
               for (name, m, r) in anchors]
        return (s, ox_, oy_, res, span, max(8.0, 0.01 * span)), ""

    while True:
        data, reason = fit(found)

        if data is None and len(found) - 1 >= MIN_ANCHORS_AFTER_DROP:
            best = None
            for candidate in found:
                trial = [e for e in found if e[0] != candidate[0]]
                t_data, _ = fit(trial)
                if t_data is None:
                    continue
                t_worst = max(t_data[3], key=lambda pair: pair[1])[1]
                if t_worst <= t_data[5] and (best is None or t_worst < best[0]):
                    best = (t_worst, candidate[0], trial)
            if best is not None:
                say(f"  {best[1]!r} is inconsistent with the others ({reason}); "
                    f"without it the remaining {len(best[2])} agree to "
                    f"{best[0]:.1f}px. Dropping it and continuing.")
                found = best[2]
                continue

        if data is None:
            say(f"\nCalibration refused: {reason}")
            missing = [p for p, _ in REF_ANCHORS if p not in {f[0] for f in found}]
            if missing:
                say(f"\n  found   {', '.join(repr(f[0]) for f in found)}")
                say(f"  missing {', '.join(repr(p) for p in missing)}")
                lower = [p for p in missing
                         if dict(REF_ANCHORS)[p][1] > REF_TRADE_SIZE[1] // 2]
                if lower:
                    say(f"  >> {', '.join(repr(p) for p in lower)} sit along the "
                        "Trade window's bottom edge. Missing them is why the "
                        "anchors are all bunched at the top, and it usually "
                        "means the window's lower part is off-screen, covered "
                        "by another window, or below the visible area.")
                say("\n  Fix: make the WHOLE Trade window visible -- all four "
                    "corners -- then re-run --calibrate. Re-running without "
                    "moving anything will read the same screen and refuse "
                    "again.")
            return None
        scale, ox, oy, residuals, span, allowed = data
        worst_name, worst = max(residuals, key=lambda pair: pair[1])
        if worst <= allowed:
            break
        if len(found) - 1 < MIN_ANCHORS_AFTER_DROP:
            say("\nThe anchors disagree about where the Trade window is.")
            say(f"\n  fitted from {len(found)} anchors: origin ({ox:.0f},{oy:.0f}), "
                f"scale {scale:.3f}, span {span:.0f}px")
            say(f"  tolerance {allowed:.0f}px\n")
            by_ref = {name: r for name, _, r in found}
            for name, residual in sorted(residuals, key=lambda p: p[1]):
                where = next(m_ for n_, m_, _ in found if n_ == name)
                say(f"    {name!r:12} off by {residual:7.1f}px   found at {where}")
            ref = by_ref[worst_name]
            rest = [e for e in found if e[0] != worst_name]
            clean, _ = fit(rest) if len(rest) >= 3 else (None, "")
            if clean:
                c_scale, c_ox, c_oy = clean[0], clean[1], clean[2]
                say(f"\n  Without {worst_name!r}, the rest agree on origin "
                    f"({c_ox:.0f},{c_oy:.0f}) scale {c_scale:.3f}.")
            else:
                c_scale, c_ox, c_oy = scale, ox, oy
            predicted = (int(c_ox + c_scale * ref[0]),
                         int(c_oy + c_scale * ref[1]))
            say(f"  {worst_name!r} was found at "
                f"{next(m_ for n_, m_, _ in found if n_ == worst_name)}, but they "
                f"put it at {predicted}.")
            win = (int(c_ox), int(c_oy),
                   int(c_ox + c_scale * REF_TRADE_SIZE[0]),
                   int(c_oy + c_scale * REF_TRADE_SIZE[1]))
            actual = next(m_ for n_, m_, _ in found if n_ == worst_name)
            outside = not (win[0] <= actual[0] <= win[2]
                           and win[1] <= actual[1] <= win[3])
            if outside:
                say(f"  That is OUTSIDE the window the others describe "
                    f"({win[0]},{win[1]})-({win[2]},{win[3]}), so it is a "
                    f"different {worst_name!r} on screen -- a chat line, a quest "
                    "panel, another window -- not part of the Trade window.")
                say(f"\n  Fix: close or move whatever shows {worst_name!r} at "
                    f"{actual}, or drag the Trade window clear of it, THEN "
                    "re-run --calibrate.")
            else:
                say("  That is inside the window the others describe, so the "
                    "reading is inconsistent rather than a decoy -- the window "
                    "may have moved while it was being measured. Retry with "
                    "the window held still.")
            say(f"\n  Dropping it would leave {len(found) - 1} anchors, below the "
                f"{MIN_ANCHORS_AFTER_DROP} that must survive for the remaining "
                "fit to be checkable, so this refuses rather than calibrating "
                "from a misread.")
            return None
        say(f"  {worst_name!r} is {worst:.0f}px from where the other "
            f"{len(found) - 1} put it (bar {allowed:.0f}px) - treating it as a "
            "misread and refitting without it.")
        found = [entry for entry in found if entry[0] != worst_name]

    dropped = len(REF_ANCHORS) - len(found)
    return Layout(screen=screen, origin=(int(round(ox)), int(round(oy))),
                  scale=scale, client=client,
                  measured_from=f"{len(found)} anchors fitted, worst residual "
                                f"{worst:.1f}px over a {span:.0f}px span"
                                + (f" ({dropped} not used)" if dropped else ""))


def validate_layout(layout: "Layout", verbose: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    left, top, right, bottom = layout.trade
    if left < 0 or top < 0 or right > layout.screen[0] or bottom > layout.screen[1]:
        say(f"  the Trade window would sit at {layout.trade}, partly off a "
            f"{layout.screen[0]}x{layout.screen[1]} screen.")
        return False
    if right <= left or bottom <= top:
        say("  the derived Trade window has no area.")
        return False
    boundary = layout.x(REF_DIALOG_BUTTON_MIN_X - REF_TRADE_ORIGIN[0])
    if not layout.x(REF_FUNCTION_COLUMN_X) < boundary < layout.screen[0]:
        say(f"  the dialog-button boundary ({boundary}) does not sit between "
            f"the Function column and the right edge of the screen.")
        return False
    for name in ("NPC_SEARCH_REGION", "POPUP_REGION", "TRADE_WINDOW_SEARCH"):
        ref = _REFERENCE_GEOMETRY.get(name)
        if ref is None:
            continue
        box = _clamp_box(layout.box(ref), layout.screen)
        if box[2] <= box[0] or box[3] <= box[1]:
            say(f"  the derived {name} has no area.")
            return False
    return True


def save_calibration(layout: "Layout", verbose: bool = True) -> bool:
    try:
        CALIBRATION_FILE.write_text(json.dumps({
            "screen": list(layout.screen),
            "origin": list(layout.origin),
            "scale": layout.scale,
            "client": list(layout.client) if layout.client else None,
            "measured_from": layout.measured_from,
        }, indent=2), encoding="utf-8")
        if verbose:
            print(f"Saved calibration to {CALIBRATION_FILE}")
        return True
    except OSError as exc:
        if verbose:
            print(f"Could not save the calibration ({exc}); it will have to be "
                  "measured again next run.", file=sys.stderr)
        return False


def load_calibration(verbose: bool = True) -> "Layout | None":
    try:
        data = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        layout = Layout(screen=tuple(data["screen"]), origin=tuple(data["origin"]),
                        scale=float(data["scale"]),
                        client=tuple(data["client"]) if data.get("client") else None,
                        measured_from=data.get("measured_from", "stored"))
    except (OSError, ValueError, KeyError, TypeError):
        return None

    current = current_screen_size()
    if current and tuple(layout.screen) != current:
        if verbose:
            print(f"Stored calibration was taken at {layout.screen[0]}x"
                  f"{layout.screen[1]} but this screen is {current[0]}x"
                  f"{current[1]} - re-measuring.")
        return None
    now = client_rect()
    if layout.client and now and tuple(layout.client) != tuple(now):
        if verbose:
            print("The game window has moved or been resized since the last "
                  "calibration - re-measuring.")
        return None
    return layout


def current_screen_size() -> tuple[int, int] | None:
    try:
        make_dpi_aware()
        with mss.mss() as sct:
            region, _ = resolve_monitor(sct, "primary")
            return (int(region["width"]), int(region["height"]))
    except Exception:
        return None


def calibrate(verbose: bool = True, save: bool = True) -> bool:
    def say(message: str) -> None:
        if verbose:
            print(message)

    forget_park_point()
    forget_scroll_calibration()

    say("Calibrating against the current screen and game window...")
    screen = current_screen_size()
    if screen:
        say(f"  screen {screen[0]}x{screen[1]}")
    rect = client_rect()
    say(f"  game client area: {rect if rect else 'not found'}")

    if not trade_window_open():
        say("  the Trade window is not open. Calibration measures it, so open "
            "the Agent Shop first (or run --open) and try again.")
        return False

    layout = measure_layout(verbose=verbose)
    if layout is None:
        return False
    if not validate_layout(layout, verbose=verbose):
        return False

    apply_layout(layout)
    say(f"Calibrated: {layout.describe()}")
    say(f"  Trade window   {LAYOUT.trade}")
    say(f"  Register panel {REGISTER_PANEL}")
    say(f"  Dialog buttons right of x={DIALOG_BUTTON_MIN_X}")
    say(f"  NPC search     {NPC_SEARCH_REGION}")
    if save:
        save_calibration(layout, verbose=verbose)
    return True


def ensure_calibrated(verbose: bool = True, required: bool = True) -> bool:
    if calibrate(verbose=verbose):
        return True

    screen = current_screen_size()
    client = client_rect()
    if (screen and tuple(screen) == REF_SCREEN
            and client and tuple(client) == REF_CLIENT):
        if verbose:
            print("Could not calibrate, but this screen and game window match "
                  "the reference the coordinates were measured on, so the "
                  "built-in values are used.")
        return True
    if not required:
        return False
    if not verbose:
        return False

    print("\nRefusing to click without a calibration.")
    if not screen:
        print("The screen size could not be determined.")
        return False

    if tuple(screen) == REF_SCREEN and client and tuple(client) != REF_CLIENT:
        print(f"The display matches the reference ({screen[0]}x{screen[1]}), but "
              f"the game window is {client[2] - client[0]}x{client[3] - client[1]} "
              f"at ({client[0]},{client[1]}) where the built-in coordinates "
              f"assume {REF_CLIENT[2] - REF_CLIENT[0]}x"
              f"{REF_CLIENT[3] - REF_CLIENT[1]} at "
              f"({REF_CLIENT[0]},{REF_CLIENT[1]}), so they do not apply.")
    elif tuple(screen) != REF_SCREEN:
        print(f"This screen is {screen[0]}x{screen[1]} and the built-in "
              f"coordinates were measured at {REF_SCREEN[0]}x{REF_SCREEN[1]}, "
              "so they point at the wrong pixels here.")
    print("A click that misses the UI lands in the game world, which moves "
          "your character -- so nothing will be clicked.")
    print("\nCalibration was attempted just now and failed for the reason "
          "printed above. Running --calibrate again WITHOUT CHANGING ANYTHING "
          "will read the same screen and fail identically.")
    return False


LOG_DIR = SCRIPT_DIR / "logs"
_log_handle = None
_RUN_STARTED = time.monotonic()
_RUN_STARTED_AT = datetime.now()
_run_finished = False
_INTERRUPTED = False


class _Tee:

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle
        self._started = time.monotonic()
        self._last = self._started
        self._at_line_start = True

    def split_write(self, console_text: str, log_text: str) -> None:
        for target, text in ((self._stream, console_text),
                             (self._handle, log_text)):
            try:
                target.write(text)
                target.flush()
            except Exception:
                pass
        self._at_line_start = True

    def _prefix(self) -> str:
        now = time.monotonic()
        delta = now - self._last
        self._last = now
        return f"[{now - self._started:8.1f} +{delta:6.1f}] "

    def _stamped(self, text: str) -> str:
        out = []
        for piece in text.splitlines(keepends=True):
            if self._at_line_start and piece.strip():
                out.append(self._prefix())
            out.append(piece)
            self._at_line_start = piece.endswith(("\n", "\r"))
        return "".join(out)

    def write(self, text):
        self._stream.write(text)
        try:
            self._stream.flush()
        except Exception:
            pass
        try:
            self._handle.write(self._stamped(text))
            self._handle.flush()
        except Exception:
            pass
        return len(text)

    def flush(self):
        for target in (self._stream, self._handle):
            try:
                target.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return self._stream.isatty()
        except Exception:
            return False

    def __getattr__(self, name):
        return getattr(self._stream, name)


def start_run_log(argv: list[str]) -> "Path | None":
    global _log_handle
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        started = datetime.now()
        path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}.log"
        n = 1
        while path.exists():
            n += 1
            path = LOG_DIR / f"run_{started:%Y-%m-%d_%H%M%S}_{n}.log"
        _log_handle = path.open("w", encoding="utf-8", buffering=1)
        _log_handle.write(
            f"=== trade.py run log ===\n"
            f"started   {started.isoformat(timespec='seconds')}\n"
            f"command   {' '.join(argv)}\n"
            f"script    {Path(__file__).resolve()}\n"
            f"python    {sys.version.split()[0]}\n"
            f"cwd       {Path.cwd()}\n"
            f"timing    [elapsed +delta] seconds, from a MONOTONIC clock.\n"
            f"          delta is the gap since the previous line -- i.e. how\n"
            f"          long the step that produced this line took.\n"
            f"{'=' * 60}\n")
        _log_handle.flush()
        sys.stdout = _Tee(sys.stdout, _log_handle)
        sys.stderr = _Tee(sys.stderr, _log_handle)

        def log_uncaught(kind, value, tb):
            try:
                text = "".join(traceback.format_exception(kind, value, tb))
                _log_handle.write(f"\n=== UNCAUGHT {kind.__name__} ===\n{text}")
                _log_handle.write(f"ended     {datetime.now().isoformat(timespec='seconds')}\n")
                _log_handle.flush()
            except Exception:
                pass
            sys.__excepthook__(kind, value, tb)

        sys.excepthook = log_uncaught
        return path
    except Exception:
        return None


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


_BIG_GLYPHS = {
    "A": (" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "C": (" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"),
    "D": ("#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "),
    "E": ("#####", "#    ", "#    ", "#### ", "#    ", "#    ", "#####"),
    "H": ("#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"),
    "N": ("#   #", "##  #", "# # #", "# # #", "#  ##", "#   #", "#   #"),
    "O": (" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "),
    "P": ("#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "),
    "R": ("#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"),
    "S": (" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "),
    "T": ("#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "),
}


def _big_text(word: str, scale: int = 2) -> list:
    rows = []
    for r in range(7):
        line = "  ".join("".join(ch * scale for ch in _BIG_GLYPHS[letter][r])
                         for letter in word if letter in _BIG_GLYPHS)
        rows.extend([line] * scale)
    return rows


def _end_banner(note: str) -> None:
    text = (note or "").strip()
    low = text.lower()
    if _INTERRUPTED or "keyboardinterrupt" in low:
        word, colour = "STOPPED", "33"
    elif not text or low in ("exit 0", "exit none"):
        word, colour = "DONE", "32"
    else:
        word, colour = "CRASHED", "31"

    try:
        rows = _big_text(word)
        rule = "#" * max(len(r) for r in rows)
        body = ["", "", rule, ""] + rows + ["", rule]
        if text:
            body.append(f"  {word.lower()}: {text}")
        body.append("")
        plain = "\n".join(body) + "\n"

        stream = sys.stdout
        tee = stream if isinstance(stream, _Tee) else None
        console = tee._stream if tee is not None else stream
        shown = plain
        try:
            if hasattr(console, "isatty") and console.isatty():
                esc = chr(27)
                shown = f"{esc}[1;{colour}m{plain}{esc}[0m"
        except Exception:
            shown = plain

        if tee is not None:
            tee.split_write(shown, plain)
        else:
            console.write(shown)
            console.flush()
    except Exception:
        pass


def finish_run_log(note: str = "") -> None:
    try:
        if OCR_PROFILE:
            report = ocr_profile_report()
            if report:
                print(report)
                cache = ocr_cache_stats()
                served = cache.get("hits", 0)
                asked = served + cache.get("misses", 0)
                if asked:
                    print(f"  cache: {served} of {asked} reads served without "
                          f"a launch ({100.0 * served / asked:.0f}%)")
    except Exception:
        pass

    global _run_finished
    if _run_finished:
        return
    _run_finished = True
    try:
        try:
            report = sales_report()
            if report:
                print(report)
            stock = bought_stock_report()
            if stock:
                print(stock)
            money = profit_report()
            if money:
                print(money)
        except Exception:
            pass

        ended = datetime.now()
        elapsed = time.monotonic() - _RUN_STARTED
        line = (f"Ran for {_format_duration(elapsed)}  "
                f"({_RUN_STARTED_AT:%H:%M:%S} -> {ended:%H:%M:%S})"
                + (f"  [{note}]" if note else ""))
        print(f"\n{line}")
        if _log_handle is not None:
            _log_handle.write(f"{'=' * 60}\n"
                              f"ended     {ended.isoformat(timespec='seconds')}\n")
            _log_handle.flush()
    except Exception:
        pass

    _end_banner(note)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Cabal Online automation: capture, Alz and the Agent Shop.")
    p.add_argument("--shot", action="store_true",
                   help="capture the screen to the screenshots folder")
    p.add_argument("--alz", action="store_true",
                   help="read the Alz balance from the screen")
    p.add_argument("--monitor", "-m", default="primary",
                   help="with --shot: 'primary', 'all', or a 1-based monitor number")
    p.add_argument("--monitors", action="store_true",
                   help="list the detected monitors and exit")
    p.add_argument("--outdir", type=Path, default=None,
                   help="with --shot: folder to save into")
    p.add_argument("--keep", type=int, default=1, metavar="N",
                   help="with --shot: how many recent captures to retain")
    p.add_argument("--no-prune", action="store_true",
                   help="with --shot: keep every capture instead of pruning")
    p.add_argument("--no-alz", action="store_true",
                   help="with --shot: skip reading the Alz balance")
    p.add_argument("--premium", action="store_true",
                   help="open the Agent Shop by right-clicking the key in the "
                        "inventory (last tab, slot 1x7) instead of finding the "
                        "NPC by OCR. Much faster and cannot miss, but the key "
                        "only exists on a premium account")
    p.add_argument("--ocr-engine", choices=("tesseract", "rapid", "paddle"),
                   default="tesseract",
                   help="which OCR engine reads the screen. 'rapid' and "
                        "'paddle' run IN PROCESS, which removes the ~121ms "
                        "process spawn that dominates a Tesseract read; "
                        "neither is installed by default and an absent engine "
                        "falls back to Tesseract rather than failing. Pair "
                        "with --ocr-profile to compare them")
    p.add_argument("--ocr-profile", action="store_true",
                   help="print, at the end of the run, every Tesseract launch "
                        "attributed to the reader that asked for it -- calls, "
                        "seconds, average and share. OCR is essentially the "
                        "whole cost of a cycle, and this is the only thing "
                        "that says WHERE it goes")
    p.add_argument("--debug-frames", action="store_true",
                   help="save a screenshot after EVERY input the script sends "
                        "(click, ctrl/alt/right click, Escape, each typed "
                        "number), labelled do.<action>. For tracing a sequence "
                        "that goes wrong between two logged lines. Costs about "
                        "0.45s per action and fills the corpus fast, so it is "
                        "for debugging only")
    p.add_argument("--no-record", action="store_true",
                   help="do not save frames to unit_tests/corpus during the run")
    p.add_argument("--record", action="store_true",
                   help="save frames even for read-only commands")
    p.add_argument("--calibrate", action="store_true",
                   help="measure this machine's screen and game window, and "
                        "remember the layout (open the Agent Shop first)")
    p.add_argument("--no-calibrate", action="store_true",
                   help="skip the layout check and use the built-in 2560x1440 "
                        "coordinates as-is")
    p.add_argument("--cancel", type=int, metavar="ROW",
                   help="cancel the listing on this 1-based table row")
    p.add_argument("--list", action="store_true",
                   help="show the listings currently visible")
    p.add_argument("--relist-rows", nargs="+", metavar="SPEC",
                   help="relist several rows, e.g. --relist-rows 1-10 or 1 3 5; "
                        "each is tracked by name, since rows renumber as you "
                        "go. 'all' sweeps the whole shop by scrolling and "
                        "relists every listing, however many there are")
    p.add_argument("--relist", type=int, nargs="+", metavar="N",
                   help="cancel a row then re-list it: --relist ROW [INV_ROW INV_COL]; "
                        "without a slot it follows the item to wherever it lands")
    p.add_argument("--do", nargs="+", metavar="ACTION",
                   help="run actions back to back, e.g. --do \"cancel 3\" \"register 1 1\"")
    p.add_argument("--repeat", nargs="+", metavar="ACTION",
                   help="repeat actions on a timer; needs --for and --every, "
                        "e.g. --repeat \"relist 1\" --for 60 --every 5")
    p.add_argument("--for", dest="duration", type=float, metavar="MINUTES",
                   help="with --repeat, how long to keep looping")
    p.add_argument("--every", type=float, metavar="MINUTES",
                   help="with --repeat, how often to run the actions; "
                        "0 starts each cycle as soon as the last one finishes")
    p.add_argument("--register", type=int, nargs=2, metavar=("ROW", "COL"),
                   help="list the item in this inventory slot on the Agent Shop")
    p.add_argument("--load", type=int, nargs=2, metavar=("ROW", "COL"),
                   help="Ctrl+Click an inventory slot into the shop slot, then stop")
    p.add_argument("--panel", action="store_true",
                   help="read the Register panel's price and quantity")
    p.add_argument("--clear", action="store_true",
                   help="close any dialog and return the shop slot item to the inventory")
    p.add_argument("--confirm", action="store_true",
                   help="click Confirmation through any dialogs left open by a stopped run")
    p.add_argument("--open", action="store_true",
                   help="reopen the Trade window via the Agent Shop NPC, on the Register tab")
    p.add_argument("--reset", action="store_true",
                   help="Escape out, reopen the shop and clear the slot - the "
                        "recovery a failed cycle performs")
    p.add_argument("--words", action="store_true",
                   help="dump every word OCR sees in the trade window")
    p.add_argument("--sales", nargs="?", const="24", metavar="HOURS",
                   help="print collections recorded in sales.db over the last "
                        "N hours (default 24, 'all' for everything). Reads the "
                        "database only -- safe while a run is going.")
    p.add_argument("--listings", action="store_true",
                   help="scroll the whole shop and list every listing with " 
                        "its absolute position (reads and scrolls only)")
    p.add_argument("--scroll", type=int, metavar="NOTCHES",
                   help="probe: turn the wheel over the listings table by N "
                        "notches (negative scrolls down) and report how far "
                        "the rows actually moved. Nothing else uses scrolling "
                        "yet")
    p.add_argument("--max-qty", action="store_true",
                   help="with --register, set the quantity to the maximum available")
    p.add_argument("--floor", type=int, default=None, metavar="ALZ",
                   help="with --register, abort if the suggested price is below this")
    p.add_argument("--price", type=int, default=None, metavar="ALZ",
                   help="with --register, list at exactly this price")
    p.add_argument("--qty", type=int, default=None, metavar="N",
                   help="with --register, list exactly this quantity")
    p.add_argument("--cost-floor", dest="cost_floor", action="store_true",
                   default=None,
                   help="refuse to relist below what the stock cost "
                        f"(default: {'on' if COST_FLOOR_ON_RELIST else 'off'})")
    p.add_argument("--no-cost-floor", dest="cost_floor", action="store_false",
                   help="allow relisting below what the stock cost, so a "
                        "position whose market has fallen still moves. Does "
                        "NOT affect ITEM_PRICE_FLOORS, which always apply")
    p.add_argument("--chaos", action="store_true",
                   help=f"keep {CHAOS_ROWS} Chaos Core Set bundle(s) on the "
                        f"board: buy Chaos Cores, craft them UP to Sets, "
                        f"compress and list. Runs BEFORE any other row, and "
                        f"only when the Set is more than "
                        f"{CHAOS_MARGIN_FLOOR:,} dearer per unit than the Core")
    p.add_argument("--buy", action="store_true",
                   help="restock sold-out Cores: buy Sets, convert them, "
                        "list the result (see RESTOCK_TARGET)")
    p.add_argument("--chaos-rows", type=int, default=CHAOS_ROWS, metavar="N",
                   help=f"with --chaos, how many Chaos Core Set bundles to "
                        f"keep on the board (default {CHAOS_ROWS})")
    p.add_argument("--chaos-min", type=int, default=CHAOS_RESTOCK_AT_OR_BELOW_ROWS,
                   metavar="N",
                   help=f"restock chaos when it is down to N bundle(s) or "
                        f"fewer, the same shape as the ordinary Cores "
                        f"restocking at {RESTOCK_AT_OR_BELOW_ROWS} row(s) or "
                        f"fewer. It sets WHEN, not how many: the pass then "
                        f"refills to --chaos-rows "
                        f"(default {CHAOS_RESTOCK_AT_OR_BELOW_ROWS})")
    p.add_argument("--chaos-quantity", type=int, default=CHAOS_BUY_QUANTITY,
                   metavar="K",
                   help=f"with --chaos, how many Cores to buy per missing "
                        f"bundle (default {CHAOS_BUY_QUANTITY}). This is the "
                        f"per-bundle spend: K x the Core price")
    p.add_argument("--core-min", type=int, default=RESTOCK_TARGET, metavar="N",
                   help=f"with --buy, the HARD floor on a resupply: it must "
                        f"reach this many Sets whatever size the cheapest "
                        f"bundle is, so overshooting to get there is allowed "
                        f"(default {RESTOCK_TARGET})")
    p.add_argument("--core-max", type=int, default=BUY_MAXIMUM, metavar="N",
                   help=f"with --buy, the SOFT ceiling: once the floor is met "
                        f"it keeps buying toward this, and stops when the NEXT "
                        f"bundle would take the total past it "
                        f"(default {BUY_MAXIMUM})")
    p.add_argument("--row-model", action="store_true",
                   help="keep a model of all 30 shop slots and TRUST it: one "
                        "full walk at the start, then no discovery sweeps. A "
                        "row that disagrees with the model ends the run rather "
                        "than being resynced. Without this the model still "
                        "runs and still checks itself, but only records what "
                        "it would have caught")
    p.add_argument("--buy-no-sweep", action="store_true",
                   help="with --buy, decide 'sold out' from the visible rows "
                        "alone instead of reading all 30. Much faster (the "
                        "sweep was 485s of a 1,000s cycle) but a Core listed "
                        "below the first screen is bought again")
    p.add_argument("--buy-target", type=int, default=RESTOCK_TARGET,
                   metavar="N",
                   help=f"Sets to accumulate per restock (default "
                        f"{RESTOCK_TARGET}; NOT one row's worth -- a row holds "
                        f"{CONVERT_QUANTITY}, and buying overshoots because a "
                        f"Set stacks to {SET_STACK_MAX})")
    p.add_argument("--dry-run", action="store_true",
                   help="locate everything but do not click")
    args = p.parse_args()

    if args.sales:
        spec = str(args.sales).strip().lower()
        hours = None if spec in ("all", "0") else float(spec)
        rows = sales_since(hours=hours, limit=SALES_QUERY_LIMIT)
        window = "all time" if hours is None else f"the last {hours:g}h"
        if not rows:
            print(f"No collections recorded in {window} ({SALES_DB}).")
            sys.exit(0)
        gross = sum(r[3] or 0 for r in rows)
        unmeasured = sum(1 for r in rows if not r[3])
        print(f"{len(rows)} collection(s) over {window}, "
              f"{gross:,} Alz measured")
        print(f"{'when':20} {'item':34} {'qty':>6} {'proceeds':>15}")
        print("-" * 78)
        for at, item, price, proceeds, qty, note in rows:
            shown = item if len(item) <= 34 else item[:31] + "..."
            print(f"{at:20} {shown:34} {(qty if qty else '-')!s:>6} "
                  f"{(format(proceeds, ',') if proceeds else '-'):>15}"
                  + (f"   [{note}]" if note else ""))
        if unmeasured:
            print(f"\n{unmeasured} of these could not be measured, so the "
                  f"gross above is a floor, not the whole of it.")
        sys.exit(0)

    if args.monitors:
        make_dpi_aware()
        with open_capture() as sct:
            list_monitors(sct)
        return

    if args.shot:
        make_dpi_aware()
        png, width, height, label, _ = take_screenshot(args.monitor)
        outdir = args.outdir or DEFAULT_OUTDIR
        outdir.mkdir(parents=True, exist_ok=True)
        path = unique_path(
            outdir / f"screenshot_{datetime.now():%Y-%m-%d_%H%M%S}_{label}.png")
        path.write_bytes(png)
        if not args.no_prune:
            removed = prune_screenshots(outdir, args.keep, path)
            if removed:
                print(f"Pruned {removed} older screenshot(s), keeping {args.keep}.")
        print(f"Saved {width}x{height} screenshot to: {path}")
        if not args.no_alz:
            alz = get_alz(path)
            print(f"Alz: {alz:,}  ({human(alz)})" if alz
                  else "Alz: 0 (Inventory panel not visible)")
        return

    if args.alz:
        alz = get_alz(grab())
        print(f"{alz:,} Alz  ({human(alz)})" if alz else "0 Alz (Inventory panel not visible)")
        return

    if args.repeat:
        if args.duration is None or args.every is None:
            p.error("--repeat needs both --for MINUTES and --every MINUTES")
        if args.duration <= 0:
            p.error("--for must be positive")
        if args.every < 0:
            p.error("--every cannot be negative; use --every 0 to start each "
                    "cycle as soon as the last one finishes")
        if args.every and args.every > args.duration:
            p.error(f"--every {args.every:g} is longer than --for {args.duration:g}, "
                    "so the actions would run once at most")

        shadowed = [f for f, v in (("--relist-rows", args.relist_rows),
                                   ("--relist", args.relist),
                                   ("--cancel", args.cancel),
                                   ("--do", args.do))
                    if v is not None]
        if shadowed:
            first = shadowed[0]
            inline = {"--relist-rows": f'--repeat "relist-rows '
                                       f'{" ".join(args.relist_rows or [])}"',
                      "--relist": '--repeat "relist N"',
                      "--cancel": '--repeat "cancel N"',
                      "--do": f'--repeat {" ".join(args.do or [])}'}[first]
            p.error(
                f"{' and '.join(shadowed)} cannot be combined with --repeat: "
                f"{first} runs once and exits before the loop ever starts, so "
                f"--repeat would be silently ignored. Put the action inside "
                f"--repeat instead:  {inline}")

    for name, value in (("--price", args.price), ("--qty", args.qty),
                        ("--floor", args.floor), ("--cancel", args.cancel)):
        if value is not None and value <= 0:
            p.error(f"{name} must be a positive number (got {value})")
    for name, pair in (("--register", args.register), ("--load", args.load)):
        if pair and not all(1 <= v <= GRID_SIZE for v in pair):
            p.error(f"{name} takes ROW COL, each between 1 and {GRID_SIZE}")
    if args.relist is not None:
        if len(args.relist) not in (1, 3):
            p.error("--relist takes ROW, or ROW INV_ROW INV_COL")
        if args.relist[0] <= 0:
            p.error("--relist ROW must be positive")
        if len(args.relist) == 3 and not all(1 <= v <= GRID_SIZE
                                             for v in args.relist[1:]):
            p.error(f"--relist's INV_ROW and INV_COL must be 1..{GRID_SIZE}")
    if args.relist_rows is not None:
        try:
            parse_row_spec(args.relist_rows)
        except ValueError as exc:
            p.error(f"bad --relist-rows spec: {exc}")

    always_clicks = (args.load is not None or args.clear or args.confirm
                     or args.open or args.reset or args.calibrate
                     or args.scroll is not None or args.listings)
    honours_dry_run = (args.cancel is not None or args.register is not None
                       or args.relist is not None
                       or args.relist_rows is not None
                       or args.do is not None
                       or args.repeat is not None)
    if honours_dry_run and args.dry_run:
        global NO_INPUT
        NO_INPUT = True

    if args.cost_floor is not None:
        globals()["COST_FLOOR_ON_RELIST"] = bool(args.cost_floor)

    if args.chaos_rows < 1:
        p.error("--chaos-rows must be at least 1")
    if args.chaos_rows > SHOP_ROW_CAPACITY:
        p.error(f"--chaos-rows {args.chaos_rows} is more rows than the shop "
                f"holds ({SHOP_ROW_CAPACITY})")
    if args.chaos_quantity < 1:
        p.error("--chaos-quantity must be at least 1")
    if args.core_min < 1:
        p.error("--core-min must be at least 1")
    if args.core_max < args.core_min:
        p.error(f"--core-max {args.core_max} is below --core-min "
                f"{args.core_min}; the floor cannot be above the ceiling")
    if args.chaos and args.relist_rows:
        try:
            asked = parse_row_spec(args.relist_rows)
        except Exception:
            asked = None
        if asked and args.chaos_rows > len(asked):
            p.error(f"--chaos-rows {args.chaos_rows} does not fit in the "
                    f"{len(asked)} row(s) being relisted; chaos is confined to "
                    f"those rows, so widen --relist-rows or lower --chaos-rows")
        if args.chaos_rows > SHOP_ROW_CAPACITY:
            p.error(f"--chaos-rows {args.chaos_rows} is more than the "
                    f"{SHOP_ROW_CAPACITY}-row shop.")
    if args.chaos and args.chaos_min > args.chaos_rows:
        p.error(f"--chaos-min {args.chaos_min} is above --chaos-rows "
                f"{args.chaos_rows}: the restock mark cannot be higher than "
                f"the target it refills to, or every cycle restocks")
    if args.chaos and args.chaos_min < 0:
        p.error("--chaos-min cannot be negative")

    if not args.chaos and (args.chaos_rows != CHAOS_ROWS
                           or args.chaos_quantity != CHAOS_BUY_QUANTITY
                           or args.chaos_min != CHAOS_RESTOCK_AT_OR_BELOW_ROWS):
        print("--chaos-rows / --chaos-quantity / --chaos-min have no effect "
              "without --chaos.")
    if not args.buy and (args.core_min != RESTOCK_TARGET
                         or args.core_max != BUY_MAXIMUM):
        print("--core-min / --core-max have no effect without --buy.")

    if args.row_model:
        SHOP.enforce = True
        print("--row-model is ON: the 30-slot model is TRUSTED. One full walk "
              "seeds it, then no discovery sweeps; a row that disagrees with "
              "it ends the run rather than resyncing.")
    else:
        SHOP.enforce = False

    if args.premium:
        globals()["PREMIUM_ENABLED"] = True
        row, col = PREMIUM_SHOP_KEY_SLOT
        print(f"--premium is ON: the Agent Shop opens from the key at tab "
              f"{PREMIUM_SHOP_KEY_TAB} slot ({row},{col}), not by finding the "
              f"NPC. No OCR sweep and no offset guessing.")

    if getattr(args, "ocr_engine", "tesseract") != "tesseract":
        select_ocr_engine(args.ocr_engine, verbose=True)

    if args.ocr_profile:
        globals()["OCR_PROFILE"] = True
        print("--ocr-profile is ON: every Tesseract launch is counted and "
              "timed, and the bill is printed when the run ends.")

    if args.debug_frames:
        globals()["DEBUG_ACTIONS"] = True
        if args.no_record:
            print("--debug-frames needs recording; --no-record disables it.")
        else:
            print("--debug-frames is ON: a screenshot after every input, "
                  "labelled do.<action> in the corpus. This is slow (~0.45s an "
                  "action) and fills the corpus quickly - debugging only.")

    if args.chaos:
        globals()["CHAOS_ENABLED"] = True
        globals()["CHAOS_ROWS"] = args.chaos_rows
        globals()["CHAOS_BUY_QUANTITY"] = args.chaos_quantity
        globals()["CHAOS_RESTOCK_AT_OR_BELOW_ROWS"] = args.chaos_min
        print(f"--chaos is ON: holding {CHAOS_ROWS} Chaos Core Set bundle(s) "
              f"in the batch's own rows, restocking at "
              f"{CHAOS_RESTOCK_AT_OR_BELOW_ROWS} bundle(s) or fewer, "
              f"topping up {CHAOS_BUY_QUANTITY} Core(s) at a time when short, "
              f"and only while the Set is more than {CHAOS_MARGIN_FLOOR:,} "
              f"dearer per unit than the Core.")
        print(f"    slot {CHAOS_CORE_SLOT}  "
              f"{FAVOURITE_SLOTS.get(CHAOS_CORE_SLOT, '?')}   <- bought")
        print(f"    slot {CHAOS_SET_SLOT}  "
              f"{FAVOURITE_SLOTS.get(CHAOS_SET_SLOT, '?')}   <- crafted, "
              f"compressed and listed")
        print("    (this pair trades the other way round from the Cores "
              "--buy looks after, so it has its own pass)")
        print(f"    a full refill of {CHAOS_ROWS} row(s) buys "
              f"{CHAOS_ROWS * CHAOS_BUY_QUANTITY:,} Core(s); at ~680,000 each "
              f"that is ~{CHAOS_ROWS * CHAOS_BUY_QUANTITY * 680_000:,} Alz.")
        print(f"    crafting {CHAOS_BUY_QUANTITY} takes "
              f"{craft_settle_seconds(CHAOS_BUY_QUANTITY):.0f}s, so a chaos "
              f"row reserves {chaos_row_allowance():.0f}s against the war lag.")

    if args.buy_no_sweep:
        globals()["BUY_NO_SWEEP"] = True
        if not args.buy:
            print("--buy-no-sweep has no effect without --buy.")
        else:
            print("--buy-no-sweep is ON: a Core missing from the visible rows "
                  "is treated as sold out without reading the rest of the "
                  "shop. Faster, but stock listed further down gets bought "
                  "again.")

    if args.buy:
        global BUY_ENABLED
        BUY_ENABLED = True
        if args.buy_target != RESTOCK_TARGET:
            if args.core_max != BUY_MAXIMUM and args.core_max != args.buy_target:
                p.error("--buy-target and --core-max both set the soft "
                        "ceiling and disagree; pass only --core-max")
            args.core_max = args.buy_target
            if args.core_max < args.core_min:
                p.error(f"--buy-target {args.buy_target} is below --core-min "
                        f"{args.core_min}: the soft ceiling cannot be under "
                        f"the hard floor")
            print(f"--buy-target is the old name for --core-max; using "
                  f"{args.core_max} as the soft ceiling.")
        allowed = enabled_buying_slots()
        validate_price_diff_floors()

        globals()["RESTOCK_TARGET"] = args.core_min
        globals()["BUY_MAXIMUM"] = args.core_max
        globals()["BUY_OVERSHOOT_FACTOR"] = args.core_max / max(1, args.core_min)

        managed = managed_core_slots()
        print(f"--buy is ON: {len(allowed)} of {len(managed)} managed Core(s) "
              f"enabled for resupply. A sell-out triggers buy -> convert -> "
              f"list.")
        print(f"    floor   {RESTOCK_TARGET} Sets, HARD: a resupply reaches it "
              f"whatever size row 1's bundle is, overshooting if it must.")
        print(f"    ceiling {BUY_MAXIMUM} Sets, SOFT: past the floor it keeps "
              f"buying toward this, and stops when the next bundle would pass "
              f"it.")
        for slot in managed:
            name = FAVOURITE_SLOTS[slot]
            if slot in allowed:
                saving = price_diff_floor_for(name)
                print(f"    ON   slot {slot:>2}  {name:26} "
                      f"saving required {saving:>9,}"
                      + ("" if saving == PRICE_DIFF_FLOOR
                         else f"  (default {PRICE_DIFF_FLOOR:,})"))
            else:
                print(f"    off  slot {slot:>2}  {name:26} "
                      f"ENABLE_BUYING is False; it will never be restocked")
        if not allowed:
            print("    NOTHING will be bought: every entry in ENABLE_BUYING "
                  "is off, so --buy has no effect this run.")


    clicking = always_clicks or (honours_dry_run and not args.dry_run)
    if clicking and not is_elevated():
        sys.exit(
            "Refusing to click: not running as Administrator.\n"
            "Cabal runs elevated, so once it holds the foreground Windows blocks "
            "our input entirely -- the click would move the cursor and do nothing "
            "else, leaving a stray tooltip on screen.\n"
            "Open an Administrator PowerShell and rerun, or add --dry-run."
        )

    global RECORD_ENABLED
    RECORD_ENABLED = ((args.record or clicking or args.debug_frames)
                      and not args.no_record)
    if RECORD_ENABLED:
        print(f"Recording frames to {RECORD_DIR} (--no-record to turn off).")

    if args.calibrate:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        sys.exit(0 if calibrate() else 1)

    if not args.no_calibrate and not args.open:
        if not ensure_calibrated(required=clicking):
            sys.exit(1)

        pass
    elif args.no_calibrate and clicking:
        screen = current_screen_size()
        if screen and tuple(screen) != REF_SCREEN:
            print(f"WARNING: --no-calibrate on a {screen[0]}x{screen[1]} screen. "
                  f"The built-in coordinates were measured at "
                  f"{REF_SCREEN[0]}x{REF_SCREEN[1]}, so clicks will land in the "
                  "wrong place.", file=sys.stderr)

    if args.panel:
        print(read_register_panel(grab()))
        return

    if args.reset:
        ok = prepare_for_actions()
        print(f"trade open: {trade_window_open()}, "
              f"register tab: {register_tab_open()}, "
              f"shop slot: {read_register_panel(grab())['qty_text']!r}")
        sys.exit(0 if ok else 1)

    if args.open:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        ok = open_trade_window()
        print(f"trade window open: {trade_window_open()}, "
              f"register tab: {register_tab_open()}")
        sys.exit(0 if ok else 1)

    if args.confirm:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        ok = confirm_open_dialogs()
        print(f"shop slot: {read_register_panel(grab())['qty_text']!r}, "
              f"loaded={read_register_panel(grab())['loaded']}")
        sys.exit(0 if ok else 1)

    if args.clear:
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        if dialog_kind(grab()) is not None and not close_any_dialog():
            sys.exit("Could not close the open dialog.")
        if not clear_shop_slot():
            sys.exit("Could not clear the shop slot.")
        print(f"Shop slot clear: {read_register_panel(grab())['qty_text']}")
        return

    if args.load:
        row, col = args.load
        if not focus_game():
            sys.exit("Could not bring Cabal to the foreground.")
        park_cursor()
        before = read_register_panel(grab())
        if before["loaded"]:
            sys.exit(f"The shop slot already holds an item ({before}). Clear it first.")
        centre = slot_centre(row, col)
        print(f"Ctrl+Click inventory slot ({row},{col}) at {centre}")
        ctrl_click(*centre)
        time.sleep(0.8)
        park_cursor()
        after = read_register_panel(grab())
        print(f"panel after: {after}")
        if not after["loaded"]:
            sys.exit("Nothing was loaded into the shop slot.")
        return

    if args.listings:
        found = enumerate_listings()
        if found is None:
            print("\nCould not enumerate the shop. Nothing was acted on.")
            sys.exit(1)
        floors = 0
        print(f"\n{'#':>3}  {'action':8} {'name':44} {'qty':>5} {'price':>14}")
        for index, row in found:
            floor = item_price_floor(row.name)
            if floor:
                floors += 1
            print(f"{index:3d}  {row.action:8} {row.name[:44]:44} "
                  f"{str(row.qty):>5} {money(row.price):>14}"
                  + (f"   floor {floor:,}" if floor else ""))
        live = [r for _, r in found if r.action in ("change", "receive")]
        print(f"\n{len(found)} listing(s), {len(live)} live, "
              f"{len(found) - EXPECTED_ROWS} beyond the first screen, "
              f"{floors} carrying a price floor")
        sys.exit(0)

    if args.scroll is not None:
        before = grab()
        rows_before = read_rows(before)
        print(f"before: {len(rows_before)} rows")
        for r in rows_before:
            print(f"   {r.index:2d} [{r.action:8}] {r.name[:38]:40} "
                  f"x{str(r.qty):>4} {r.price}  band {r.top}-{r.bottom}")
        centre = ((TRADE_REGION[0] + TRADE_REGION[2]) // 2,
                  (TRADE_REGION[1] + TRADE_REGION[3]) // 2)
        print(f"\nscrolling {args.scroll:+d} notch(es) at {centre}")
        record("scroll.before", before, notches=args.scroll)
        scroll_wheel(*centre, args.scroll)
        time.sleep(0.8)
        park_cursor()
        after = grab()
        rows_after = read_rows(after)
        record("scroll.after", after, notches=args.scroll)
        print(f"\nafter: {len(rows_after)} rows")
        for r in rows_after:
            print(f"   {r.index:2d} [{r.action:8}] {r.name[:38]:40} "
                  f"x{str(r.qty):>4} {r.price}  band {r.top}-{r.bottom}")

        def key(r):
            return (r.name, r.price, r.qty)
        b = [key(r) for r in rows_before]
        a = [key(r) for r in rows_after]
        fits = []
        for d in range(-len(b), len(b) + 1):
            overlap = [(i, i + d) for i in range(len(b))
                       if 0 <= i + d < len(a)]
            if len(overlap) >= 3 and all(b[i] == a[j] for i, j in overlap):
                fits.append((-d, len(overlap)))
        if len(fits) == 1:
            shift = fits[0][0]
        elif len(fits) > 1:
            shift = None
            print(f"\nAMBIGUOUS: {len(fits)} offsets fit equally well "
                  f"{[f[0] for f in fits]} - duplicate listings make the view "
                  "position unrecoverable from content alone.")
        else:
            shift = None
        print(f"\nrows moved: {shift if shift is not None else 'COULD NOT TELL'}")
        if fits:
            print(f"  offsets that fit: {fits}  (offset, overlapping rows)")
        if shift == 0:
            print("  the view did not move - the list may be at its end, or the")
            print("  wheel event did not reach the table.")
        elif shift is None:
            print("  no consistent overlap of 3+ rows. Either the whole page")
            print("  changed, or a listing changed under us. Do NOT act on")
            print("  positions in this state.")
        return

    if args.words:
        for w in sorted(find_words(grab(), TRADE_REGION), key=lambda w: (w.top, w.left)):
            print(f"{w.centre}  conf={w.conf:5.1f}  {w.text!r}")
        return

    if args.list:
        rows = await_rows()
        if not rows:
            print("No listings visible.")
        for row in rows:
            print(f"row {row.index:2d}: [{row.action:8s}] button={row.change}  {row.name!r}")
        return

    _shape = read_run_shape(verbose=True)
    if _shape and not args.repeat and not args.relist_rows and not args.relist:
        spec = _shape["relist_rows"]
        minutes = int(_shape.get("for_minutes", 600))
        every = int(_shape.get("every_minutes", 0))
        if _shape.get("premium") and not args.premium:
            globals()["PREMIUM_ENABLED"] = True
            print("  config.json: premium shop entry is ON.")
        if _shape.get("debug_frames") and not args.debug_frames:
            globals()["DEBUG_ACTIONS"] = True
            print("  config.json: debug frames are ON.")
        if _shape.get("row_model") and not args.row_model:
            SHOP.enforce = True
            print("  config.json: --row-model is ON. The 30-slot model is "
                  "TRUSTED -- one seeding walk, then no discovery sweeps, and "
                  "a row that disagrees ENDS THE RUN rather than resyncing.")
        print(f"  config.json: looping 'relist-rows {spec}' for {minutes} min, "
              f"every {every} min.")
        record("config.run_from_file", spec=spec, minutes=minutes, every=every)
        sys.exit(0 if run_loop([f"relist-rows {spec}"], float(minutes),
                               float(every), dry_run=args.dry_run,
                               verbose=True) else 1)
    if _shape and (args.repeat or args.relist_rows or args.relist):
        print(f"  {LIVE_CONFIG_FILE.name} has a run block, but this command "
              f"line asks for a specific action - using the command line for "
              f"THIS run. The file's knobs still apply and are still re-read "
              f"every cycle.")

    if args.relist_rows:
        try:
            wanted = parse_row_spec(args.relist_rows)
        except ValueError as exc:
            p.error(f"bad --relist-rows spec: {exc}")
        every = wants_all_rows(args.relist_rows)
        if not wanted and not every:
            p.error("--relist-rows needs at least one row, or 'all'")
        try:
            ok = relist_rows(wanted, dry_run=args.dry_run, all_rows=every)
        except ShopEmpty as exc:
            print(f"SOLD OUT: {exc}")
            sys.exit(0)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            if not args.dry_run:
                leave_shop()
        sys.exit(0 if ok else 1)

    if args.relist:
        if len(args.relist) not in (1, 3):
            p.error("--relist takes ROW, or ROW INV_ROW INV_COL")
        slot = tuple(args.relist[1:]) if len(args.relist) == 3 else (None, None)
        try:
            outcome = relist(args.relist[0], *slot, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            if not args.dry_run:
                leave_shop()
        print(f"outcome: {outcome}")
        sys.exit(0 if outcome != FAILED else 1)

    if args.repeat:
        sys.exit(0 if run_loop(args.repeat, args.duration, args.every,
                               dry_run=args.dry_run) else 1)

    if args.do:
        try:
            ok = run_sequence(args.do, dry_run=args.dry_run)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            if not args.dry_run:
                leave_shop()
        sys.exit(0 if ok else 1)

    if args.register is not None:
        try:
            ok = register_item(*args.register, dry_run=args.dry_run,
                               maximise_qty=True if args.max_qty else None,
                               price_floor=args.floor,
                               floor_reason="--floor" if args.floor else "",
                               force_price=args.price, force_qty=args.qty)
        except FatalAbort as exc:
            sys.exit(f"FATAL: {exc}")
        except (PermissionError, Aborted) as exc:
            sys.exit(f"Blocked: {exc}")
        finally:
            if not args.dry_run:
                leave_shop()
        sys.exit(0 if ok else 1)

    if args.cancel is None:
        p.error("nothing to do. Capture: --shot, --alz, --monitors. Read: "
                "--list, --panel, --words. Act: --open, --relist, --relist-rows, "
                "--cancel, --register, --load, --clear, --confirm, --reset, "
                "--do, --repeat. See --help.")

    try:
        ok = cancel_item(args.cancel, dry_run=args.dry_run)
    except PermissionError as exc:
        sys.exit(f"Blocked: {exc}")
    finally:
        if not args.dry_run:
            leave_shop()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    atexit.register(finish_run_log)

    _log_path = start_run_log(sys.argv)
    if _log_path:
        print(f"Logging this run to {_log_path}")
    try:
        main()
    except SystemExit as exc:
        finish_run_log(f"exit {exc.code}")
        raise
    except BaseException as exc:
        finish_run_log(f"{type(exc).__name__}")
        raise
    else:
        finish_run_log()
