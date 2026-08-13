"""The Purchase tab: the sort, the favourite slots, and the offer rows.

Spec: purchase.md

This is where prices are read. Two things gate every read and neither is
optional:

  The SORT. "Row 1 is the cheapest" is only true under Price: Low to High, and
  the control is a dropdown a human can change. It is read, never assumed.

  The TAB. A favourite-slot coordinate with no Purchase tab under it is a
  click into the listings table or into the 3D world.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from PIL import Image

from . import geometry as geo
from . import ocr
from . import screen
from . import shop
from .layout import Layout

# How many times a favourite search is re-pressed before giving up. The search
# genuinely does fail to run sometimes -- the click lands while the client is
# waiting on the server and nothing happens.
SEARCH_TRIES = 5
# How long to let the results settle after pressing a slot.
SEARCH_SETTLE = 3.0
# How long to wait for the sort menu to be legible after opening it.
SORT_MENU_TIMEOUT = 2.0
SORT_TRIES = 3

# The direction pattern is a fact about the game's label, so it lives in
# geometry beside the control it reads. See geometry.SORT_DIRECTION for why a
# substring test cannot do this job.
_SORT_DIRECTION = re.compile(geo.SORT_DIRECTION, re.I)

_PRICE = re.compile(r"[0-9][0-9,]*")
# 'Chaos Core Set X 148' -- the count lives in the NAME for bundled items, and
# it is the only place the pack size appears.
_PACK_IN_NAME = re.compile(r"\bx\s*([0-9][0-9,]*)\s*$", re.I)
# What may legally follow a bound item's name in a listing: a bundle count and
# nothing else. See offers_match_slot.
_BUNDLE_SUFFIX = re.compile(r"x\d+")


@dataclass(frozen=True)
class Offer:
    """One row of the offer table."""
    row: int            # 1-based, as shown
    name: str
    price: int          # the WHOLE listing, not per unit
    pack: int           # how many units the listing contains
    available: int      # how many such listings are on offer

    @property
    def unit_price(self) -> "int | None":
        """What one unit costs, or None when the row did not read.

        None rather than a fallback. A pack that did not read is not a pack of
        one: treating it as one would inflate a 148-unit bundle's unit price
        148-fold, in the direction that makes a bad trade look good.
        """
        if self.price <= 0 or self.pack < 1:
            return None
        return self.price // self.pack


# --------------------------------------------------------------------------
# The sort
# --------------------------------------------------------------------------

def sorted_low_to_high(layout: Layout,
                       frame: "ocr.Frame | None" = None) -> bool:
    """True when the results are sorted Price: Low to High.

    Delegated to shop.read_state, which reads the whole control band in one
    OCR. Asking separately would pay a second 70ms process launch for words
    that have already been recognised.

    Fails closed: an unread direction, or no "Price:" at all, is False.
    """
    return shop.read_state(layout, frame).sorted_low_to_high


def _sort_menu_rows(layout: Layout) -> dict:
    """Where each open menu option sits: {"low": (x, y), "high": (x, y)}.

    The offers table shows through the same band when the menu is shut, so a
    row is only accepted when it names BOTH directions -- the table's own
    header is "QTY | Price | ..." and names neither. Half a label is refused:
    clicking a row that was only partly read is how a menu click lands on the
    table underneath it.
    """
    frame = ocr.Frame(screen.grab())
    words = frame.words(layout.cropped(geo.SORT_OPTIONS),
                        upscale=shop.state_upscale(layout), min_conf=45.0)
    rows: dict = {}
    for line in ocr.text_lines(words, max(4, layout.length(10))):
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


def set_sort_low_to_high(layout: Layout, verbose: bool = True) -> bool:
    """Put the sort on Price: Low to High and confirm it landed."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    for attempt in range(1, SORT_TRIES + 1):
        if sorted_low_to_high(layout):
            say("  the sort is already Price: Low to High.")
            return True
        say(f"  the sort is not Price: Low to High; opening the dropdown "
            f"(try {attempt} of {SORT_TRIES}).")
        screen.focus_game()
        screen.click(*layout.point(geo.SORT_BUTTON))
        deadline = time.monotonic() + SORT_MENU_TIMEOUT
        rows: dict = {}
        while True:
            rows = _sort_menu_rows(layout)
            if "low" in rows or time.monotonic() >= deadline:
                break
            time.sleep(0.2)
        if "low" not in rows:
            say("  the sort menu did not become legible.")
            continue
        say(f"  selecting 'Price: Low to High' at {rows['low']}.")
        screen.click(*rows["low"])
        time.sleep(0.4)
        if sorted_low_to_high(layout):
            say("  the sort now reads Price: Low to High.")
            return True
    say("  could not confirm the Price: Low to High sort.")
    return False


# --------------------------------------------------------------------------
# Readiness
# --------------------------------------------------------------------------

def purchase_ready(layout: Layout, verbose: bool = True,
                   frame: "ocr.Frame | None" = None) -> bool:
    """Every precondition for clicking anything on the Purchase tab.

    ONE screenshot and ONE OCR, three questions. All three markers live in the
    same band across the top of the window, so asking them separately paid
    three process launches for one set of words.

    STILL CHECKED BEFORE EACH CLICK. Making it cheap is not the same as making
    it optional: the window can close between one click and the next, and the
    saving from trusting a stale answer is 70ms against a click into the 3D
    world that walks the character away from the shop.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    state = shop.read_state(layout, frame or ocr.Frame(screen.grab()))
    if not state.window_open:
        say("  the Trade window is not open - refusing to click, the game "
            "world is underneath.")
        return False
    if not state.purchase_tab:
        say("  the Trade window is not on the Purchase tab - refusing to "
            "click Purchase-tab coordinates on another tab.")
        return False
    if not state.sorted_low_to_high:
        say("  the results are not sorted Price: Low to High, so 'row 1 is "
            "the cheapest' does not hold. Refusing.")
        return False
    return True


# --------------------------------------------------------------------------
# Reading the offer table
# --------------------------------------------------------------------------

def _number(text: str) -> "int | None":
    match = _PRICE.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _pack_from_name(name: str) -> int:
    """How many units a listing holds, from its NAME.

    Bundled items carry their count in the name -- 'Chaos Core Set X 148' --
    and it appears nowhere else on the row. An unbundled item has no such
    suffix and is a pack of one.
    """
    match = _PACK_IN_NAME.search(name or "")
    if not match:
        return 1
    try:
        return max(1, int(match.group(1).replace(",", "")))
    except ValueError:
        return 1


def read_offer_rows(layout: Layout,
                    frame: "ocr.Frame | None" = None,
                    rows: int = 1) -> "list[Offer]":
    """The first `rows` offer rows, numbered from 1 as shown.

    DEFAULTS TO ONE, because that is what every caller in this flow wants.
    Each row costs its own OCR launch -- there is no way to read ten rows in
    one pass without a crop tall enough to interleave them -- so reading the
    nine rows nobody looks at is nine wasted launches per search, and there
    are up to five attempts per slot and two slots per call.

    Each row is OCR'd from its own horizontal strip and the words are split
    into cells by x. One banded read per row yields name, quantity and price
    together; they are not three separate reads.

    Rows that cannot be parsed are SKIPPED, not padded, so the caller must
    select by `Offer.row` rather than by list position.
    """
    frame = frame or ocr.Frame(screen.grab())
    upscale = shop.state_upscale(layout)
    half = layout.length(geo.ROW_HALF)
    out: "list[Offer]" = []
    for index in range(rows):
        centre_y = layout.y(geo.ROW_TOP + index * geo.ROW_PITCH)
        band = layout.clamp((layout.x(geo.ROW_BAND_X[0]), centre_y - half,
                             layout.x(geo.ROW_BAND_X[1]), centre_y + half))
        words = frame.words(band, upscale=upscale, min_conf=30.0)
        if not words:
            continue
        name_max = layout.x(geo.NAME_MAX_X)
        price_lo = layout.x(geo.PRICE_X[0])
        price_hi = layout.x(geo.PRICE_X[1])
        name = " ".join(w.text for w in sorted(words, key=lambda w: w.left)
                        if w.centre[0] < name_max).strip()
        price_text = " ".join(w.text for w in sorted(words, key=lambda w: w.left)
                              if price_lo <= w.centre[0] <= price_hi)
        qty_text = " ".join(w.text for w in sorted(words, key=lambda w: w.left)
                            if name_max <= w.centre[0] < price_lo)
        price = _number(price_text)
        if not name or price is None or price < geo.MIN_PLAUSIBLE_PRICE:
            continue
        out.append(Offer(row=index + 1, name=name, price=price,
                         pack=_pack_from_name(name),
                         available=_number(qty_text) or 1))
    return out


# --------------------------------------------------------------------------
# The favourite slots
# --------------------------------------------------------------------------

def favourite_point(layout: Layout, slot: int) -> "tuple[int, int]":
    """Where favourite slot `slot` sits on screen. 1-based."""
    if not 1 <= slot <= geo.FAVOURITE_COUNT:
        raise ValueError(f"favourite slot {slot} is outside 1..{geo.FAVOURITE_COUNT}")
    return layout.point((geo.FAVOURITE_FIRST[0] + (slot - 1) * geo.FAVOURITE_PITCH,
                         geo.FAVOURITE_FIRST[1]))


def _canonical(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").casefold())


def offers_match_slot(slot: int, offers: "list[Offer]") -> bool:
    """True when these results plausibly belong to the slot just pressed.

    Stale rows read as a real answer are worse than no answer: they look
    exactly like a successful search of a DIFFERENT item, and every price
    decision downstream is then about the wrong thing.
    """
    want = _canonical(geo.FAVOURITE_SLOTS.get(slot, ""))
    if not want or not offers:
        return False
    head = _canonical(offers[0].name)

    # A PREFIX, AND THEN ONLY A BUNDLE COUNT. Containment is not enough and
    # this is the bug it hides:
    #
    #   slot 3 is 'Chaos Core'      -> 'chaoscore'
    #   slot 4 lists 'Chaos Core Set X 148' -> 'chaoscoresetx148'
    #
    # 'chaoscore' IS contained in 'chaoscoresetx148', so a slot 3 search that
    # never ran would accept slot 4's stale results and price Cores at the
    # Sets' price. The same shape as 'Force Core(High)' matching
    # 'Force Core(Highest)', which this file's geometry table contains as
    # slots 7 and 1.
    #
    # So: the listing must START with the bound name, and whatever follows
    # must be a bundle count and nothing else. More letters mean a different,
    # longer item.
    if not head.startswith(want):
        return False
    rest = head[len(want):]
    return rest == "" or _BUNDLE_SUFFIX.fullmatch(rest) is not None


def run_favourite_search(layout: Layout, slot: int,
                         tries: int = SEARCH_TRIES,
                         verbose: bool = True) -> "list[Offer]":
    """Press favourite `slot` and return what it found, or [].

    EMPTY rather than whatever happens to be on screen when the search cannot
    be confirmed. The three ways this returns [] are logged distinctly -- the
    tab was not ready, the results never refreshed, or the search ran and the
    market is empty -- because they call for opposite responses from a human.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    for attempt in range(1, tries + 1):
        # RE-CHECKED EVERY TIME, not once before the loop.
        if not purchase_ready(layout, verbose=verbose):
            say(f"  slot {slot}: the Purchase tab is not ready (tab, sort or "
                f"the window itself) - the search was never run.")
            return []
        x, y = favourite_point(layout, slot)
        screen.focus_game()
        # APPROACHED FROM ABOVE so the pointer ENTERS the button. A move to the
        # pixel the cursor already occupies raises no event, and a control that
        # arms on hover is then never armed.
        screen.move_mouse(x, y - layout.length(45))
        time.sleep(0.2)
        screen.click(x, y)
        time.sleep(SEARCH_SETTLE)
        # ROW 1 ONLY. Nothing in this flow looks further down, and each
        # extra row is another process launch.
        offers = read_offer_rows(layout, rows=1)
        if offers and offers_match_slot(slot, offers):
            say(f"  slot {slot} ({geo.FAVOURITE_SLOTS.get(slot, '?')}): "
                f"{len(offers)} offer(s)")
            return offers
        sample = offers[0].name if offers else "(nothing)"
        say(f"  slot {slot}: the results still show {sample!r} - the search "
            f"did not run (attempt {attempt}/{tries})")
    return []
