"""Per-unit price difference between two favourite slots.

Spec: get_price_diff.md

Click favourite slot A, take ROW 1, click favourite slot B, take ROW 1, and
return the difference of their PER-UNIT prices. Row 1 is the rule and is never
relaxed. Per-unit is what makes the two sides comparable when the slots hold
different pack sizes.

Standalone: this package talks to Windows, Tesseract and the Cabal client
directly and imports nothing from trade.py.

DRIVES THE GAME. Reading the Purchase tab means putting the client on it and
pressing a favourite slot, which is a real click at real coordinates. Nothing
here buys anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cabal import geometry as geo          # noqa: E402
from cabal import calibrate, purchase, screen, shop   # noqa: E402
from cabal.layout import Layout            # noqa: E402


__all__ = ["get_price_diff", "price_per_unit"]


def price_per_unit(offer) -> "int | None":
    """What one unit of `offer` costs, or None if the listing did not read.

    The Price column holds the price of the WHOLE listing. A row reading
    'Item X 10' at 7,400,000 is ten units at 740,000 each, and comparing that
    7,400,000 against a single-unit listing is not a comparison.

    DIVIDED EVEN WHEN pack IS 1. Dividing by one is free, and making the
    division conditional is exactly how one side of a subtraction ends up
    normalised while the other does not.
    """
    if offer is None:
        return None
    return offer.unit_price


def _row_one(offers: list):
    """The offer sitting at row 1, or None.

    Selected BY ITS ROW NUMBER, not by list position. read_offer_rows numbers
    what it finds and skips rows it could not read, so offers[0] is the first
    row that PARSED rather than the first row on screen. The rule is about the
    row the game is showing, so that is what is matched -- and a row 1 that did
    not read must refuse, not silently promote row 2.
    """
    for offer in offers or []:
        if offer.row == 1:
            return offer
    return None


def _read_slot_row_one(layout: Layout, slot: int,
                       verbose: bool = True) -> "int | None":
    """Search `slot` and return row 1's per-unit price, or None."""
    offers = purchase.run_favourite_search(layout, slot, verbose=verbose)
    if not offers:
        # run_favourite_search has already said WHICH of the three failures
        # this was; all three are the same unknown here.
        return None
    row_one = _row_one(offers)
    if row_one is None:
        if verbose:
            print(f"  slot {slot}: {len(offers)} offer(s) read, but row 1 was "
                  f"not among them - refusing to substitute another row.")
        return None
    unit = price_per_unit(row_one)
    if unit is None:
        if verbose:
            print(f"  slot {slot}: row 1 is {row_one.name!r} but its price or "
                  f"pack did not read - no price for this side.")
        return None
    if verbose:
        print(f"  slot {slot} ({geo.FAVOURITE_SLOTS.get(slot, '?')}): row 1 "
              f"{row_one.name!r} at {row_one.price:,} for {row_one.pack} "
              f"-> {unit:,}/unit")
    return unit


def get_price_diff(A: int, B: int, in_shop: bool = False,
                   verbose: bool = True,
                   layout: "Layout | None" = None) -> "int | None":
    """Per-unit price of favourite slot A minus that of favourite slot B.

    Positive when A's unit price is HIGHER than B's. For a buy-low-craft-up
    trade pass the crafted output as A and the raw input as B, and a positive
    return is the profit per unit.

    Returns None -- never 0 -- for every failure. Zero is a real answer, two
    items at the same unit price, and a caller comparing the result against a
    threshold cannot tell a measured zero from a failed read.

    `in_shop` is the caller's belief, treated as a hint and always verified.
    A wrong flag does not produce a wrong number, it produces a click into the
    game world.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    for label, slot in (("A", A), ("B", B)):
        if slot not in geo.FAVOURITE_SLOTS:
            say(f"Slot {label}={slot!r} is not a favourite slot "
                f"({sorted(geo.FAVOURITE_SLOTS)}). Nothing was clicked.")
            return None

    # MEASURED BEFORE ANYTHING IS CLICKED. Every coordinate below is a
    # reference value that means nothing until the window has been located.
    if layout is None:
        layout = calibrate.calibrated_layout(verbose=verbose)
    if layout is None:
        say("Could not measure the Trade window, so no coordinate is "
            "trustworthy. Nothing was clicked.")
        return None

    # THE HINT IS CHECKED, NOT TRUSTED.
    was_open = shop.trade_window_open(layout)
    if in_shop and not was_open:
        say("in_shop=True, but the Trade window is not open.")
    opened_here = False
    if not was_open:
        if not shop.open_agent_shop(layout, verbose=verbose):
            say("The Agent Shop is not open and this flow cannot open it; "
                "no prices were read.")
            return None
        opened_here = True

    was_register = (not opened_here) and shop.register_tab_open(layout)

    try:
        if not shop.open_purchase_tab(layout, verbose=verbose):
            say("Could not reach the Purchase tab; no prices were read.")
            return None
        # Set once here so the dropdown is not opened twice; RE-CONFIRMED
        # before each search by purchase_ready() inside run_favourite_search,
        # which refuses to click when the sort is wrong.
        if not purchase.set_sort_low_to_high(layout, verbose=verbose):
            say("Could not confirm the Price: Low to High sort, so 'row 1 is "
                "the cheapest' does not hold. Refusing to read prices.")
            return None

        a_unit = _read_slot_row_one(layout, A, verbose=verbose)
        if a_unit is None:
            say(f"No usable price for slot A ({A}).")
            return None
        b_unit = _read_slot_row_one(layout, B, verbose=verbose)
        if b_unit is None:
            say(f"No usable price for slot B ({B}).")
            return None

        diff = a_unit - b_unit
        say(f"  {geo.FAVOURITE_SLOTS.get(A, A)} {a_unit:,}/unit  -  "
            f"{geo.FAVOURITE_SLOTS.get(B, B)} {b_unit:,}/unit  =  {diff:,}")
        return diff
    finally:
        # Runs on every path, including the refusals above. Leaving the client
        # on the Purchase tab is not harmless: a caller that reads listings
        # next would scroll the OFFERS instead.
        try:
            if opened_here:
                shop.close_shop(layout, verbose=verbose)
            elif was_register and not shop.register_tab_open(layout):
                shop.open_register_tab(layout, verbose=verbose)
        except Exception as exc:  # noqa: BLE001 - tidying must not become the story
            say(f"Note: could not restore the window state ({exc}).")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        print("usage: python src/get_price_diff.py <slot A> <slot B>")
        for index, name in sorted(geo.FAVOURITE_SLOTS.items()):
            print(f"  {index:>2}  {name}")
        raise SystemExit(2)
    a, b = int(sys.argv[1]), int(sys.argv[2])
    print(f"About to click favourite slots {a} and {b} in the live client.")
    result = get_price_diff(a, b, in_shop=True, verbose=True)
    if result is None:
        print("RESULT: None - the difference could not be measured.")
        raise SystemExit(1)
    print(f"RESULT: {result:,}")
