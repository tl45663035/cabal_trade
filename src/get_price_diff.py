"""Per-unit price difference between two favourite slots.

Implements src/get_price_diff.md. Read that first: the rules there are not
style preferences, and two of them exist because breaking them cost money.

The one-line summary: click favourite slot A, take ROW 1, click favourite slot
B, take ROW 1, and return the difference of their PER-UNIT prices. Row 1 is the
rule and is never relaxed. Per-unit is what makes the two sides comparable when
the slots hold different pack sizes.

DRIVES THE GAME. Every function below clicks. There is no dry mode: reading
the Purchase tab means putting the client on it and pressing a favourite slot,
which is a real click at real coordinates. Nothing here buys anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

# trade.py lives one directory up and is the only source of the primitives
# below. Inserted rather than assumed on the path so this module works when it
# is imported from anywhere -- a test runner, the REPL, or the root script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import trade  # noqa: E402


__all__ = ["get_price_diff", "price_per_unit", "PriceDiffError"]


class PriceDiffError(Exception):
    """Never raised by get_price_diff. Present for callers that want to wrap."""


def price_per_unit(offer) -> "int | None":
    """What one unit of `offer` costs, or None if the listing did not read.

    The Price column holds the price of the WHOLE listing. A row reading
    'Item X 10' at 7,400,000 is ten units at 740,000 each, and comparing that
    7,400,000 against a single-unit listing is not a comparison at all.

    DIVIDED EVEN WHEN pack IS 1. Dividing by one is free, and making the
    division conditional is exactly how one side of a subtraction ends up
    normalised while the other does not.

    None rather than 0 for an unreadable listing: see get_price_diff.
    """
    if offer is None:
        return None
    price = getattr(offer, "price", None)
    pack = getattr(offer, "pack", None)
    if not price or price <= 0:
        return None
    # A pack that did not read is NOT a pack of one. Treating it as one turns a
    # 148-unit bundle into a single unit and inflates its unit price by 148x,
    # which is the same class of error as not dividing at all -- and it fails
    # in the expensive direction, making a bad trade look good.
    if pack is None or pack < 1:
        return None
    return int(price) // int(pack)


def _row_one(offers: list) -> "object | None":
    """The offer sitting at row 1, or None.

    Selected BY ITS ROW NUMBER, not by list position. read_purchase_rows
    numbers what it finds (`Offer(row=i + 1, ...)`) and the two agree today,
    but a filter added anywhere upstream would silently make offers[0] the
    first SURVIVING row rather than the first row on screen. The spec's rule is
    about the row the game is showing, so that is what is matched.
    """
    for offer in offers or []:
        if getattr(offer, "row", None) == 1:
            return offer
    return None


def _read_slot_row_one(slot: int, verbose: bool = True) -> "int | None":
    """Search `slot` and return row 1's per-unit price, or None.

    The tab, the sort and the window are re-verified before EVERY attempt --
    not here, but inside run_favourite_search, which calls purchase_ready() at
    the top of each of its five tries. That is the guarantee the spec asks for
    and there is no reason to duplicate the read: purchase_ready() grabs a
    frame and checks the window, the panel, the tab AND the sort together, and
    refuses the click if any of them is wrong.

    It matters more than it looks. A favourite coordinate with no window under
    it is not a failed search, it is a move order into the 3D world -- which is
    how a capture loop once clicked eighty times into the game and walked the
    character away from the NPC.
    """
    offers = trade.run_favourite_search(slot, verbose=verbose)
    if not offers:
        # run_favourite_search returns [] for three different conditions and
        # prints which one it was: the tab was not ready and the search never
        # ran, the results never refreshed, or the search ran and the market is
        # genuinely empty. They need opposite responses from a human, so the
        # distinction is preserved in the log even though all three are the
        # same None here.
        return None
    row_one = _row_one(offers)
    if row_one is None:
        if verbose:
            print(f"  slot {slot}: {len(offers)} offer(s) read, but none of "
                  f"them is row 1 - refusing to substitute another row.")
        return None
    unit = price_per_unit(row_one)
    if unit is None:
        if verbose:
            print(f"  slot {slot}: row 1 is {row_one.name!r} but its price or "
                  f"quantity did not read - no price for this side.")
        return None
    if verbose:
        pack = getattr(row_one, "pack", 1)
        print(f"  slot {slot} ({trade.FAVOURITE_SLOTS.get(slot, '?')}): row 1 "
              f"{row_one.name!r} at {row_one.price:,} for {pack} "
              f"-> {unit:,}/unit")
    return unit


def get_price_diff(A: int, B: int, in_shop: bool = False,
                   verbose: bool = True) -> "int | None":
    """Per-unit price of favourite slot A minus that of favourite slot B.

    Positive when A's unit price is HIGHER than B's. For a buy-low-craft-up
    trade pass the crafted output as A and the raw input as B, and a positive
    return is the profit per unit.

    Returns None -- never 0 -- for every failure. Zero is a real answer, two
    items at the same unit price, and a caller comparing the result against a
    threshold cannot tell a measured zero from a failed read. Anything that
    goes wrong is an UNKNOWN difference, and the caller decides what to do
    about not knowing.

    `in_shop` is the caller's belief, treated as a hint and always verified.
    A wrong flag does not produce a wrong number, it produces a click into the
    game world, so the window is measured rather than assumed. If this call
    opens the shop it closes it again before returning; if it merely switches
    the tab of a shop that was already open, it puts the tab back.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    for name, slot in (("A", A), ("B", B)):
        if slot not in trade.FAVOURITE_SLOTS:
            say(f"Slot {name}={slot!r} is not a favourite slot "
                f"({sorted(trade.FAVOURITE_SLOTS)}). Nothing was clicked.")
            return None

    # THE HINT IS CHECKED, NOT TRUSTED. `in_shop=True` with the window shut is
    # the dangerous direction, so the measurement decides and the flag only
    # saves a redundant open when it agrees.
    was_open = trade.trade_window_open()
    if in_shop and not was_open:
        say("in_shop=True, but the Trade window is not open - opening it "
            "rather than clicking where it should be.")
    opened_here = False
    if not was_open:
        if not trade.ensure_shop_ready(verbose=verbose):
            say("Could not open the Agent Shop; no prices were read.")
            return None
        opened_here = True

    # Whether the caller was sitting on the Register tab, so it can be put
    # back. open_trade_window lands on Register, so a shop this call opened is
    # closed entirely instead and this does not apply.
    was_register = (not opened_here) and trade.register_tab_open()

    try:
        if not trade.open_purchase_tab(verbose=verbose):
            say("Could not reach the Purchase tab; no prices were read.")
            return None
        # Sorted once here so the dropdown is not opened twice; it is
        # RE-CONFIRMED before each search by purchase_ready() inside
        # run_favourite_search, which refuses to click when the sort is wrong.
        # Low to High sorts by listing TOTAL, so an unconfirmed sort means row
        # 1 may be the dearest offer on the board rather than the cheapest.
        if not trade.set_purchase_sort_low_to_high(verbose=verbose):
            say("Could not confirm the Price: Low to High sort, so 'row 1 is "
                "the cheapest' does not hold. Refusing to read prices.")
            return None

        a_unit = _read_slot_row_one(A, verbose=verbose)
        if a_unit is None:
            say(f"No usable price for slot A ({A}).")
            return None
        b_unit = _read_slot_row_one(B, verbose=verbose)
        if b_unit is None:
            say(f"No usable price for slot B ({B}).")
            return None

        diff = a_unit - b_unit
        say(f"  {trade.FAVOURITE_SLOTS.get(A, A)} {a_unit:,}/unit  -  "
            f"{trade.FAVOURITE_SLOTS.get(B, B)} {b_unit:,}/unit  "
            f"=  {diff:,}")
        return diff
    finally:
        # Runs on every path, including the refusals above. Leaving the client
        # parked on the Purchase tab is not harmless: the listings table only
        # exists on Register, and a caller that reads rows next would scroll
        # the OFFERS instead -- and the whole buying design rests on row 1
        # being the cheapest.
        try:
            if opened_here:
                trade.leave_shop(verbose=verbose)
            elif was_register and not trade.register_tab_open():
                trade.open_trade_window(verbose=verbose)
        except Exception as exc:  # noqa: BLE001 - tidying must not become the story
            say(f"Note: could not restore the window state ({exc}).")


if __name__ == "__main__":
    # Deliberately has no defaults. This clicks the real game, so it will not
    # run without being told exactly which two slots to compare.
    if len(sys.argv) < 3:
        print(__doc__)
        print("usage: python src/get_price_diff.py <slot A> <slot B>")
        print(f"  slots: {trade.FAVOURITE_SLOTS}")
        raise SystemExit(2)
    a, b = int(sys.argv[1]), int(sys.argv[2])
    print(f"About to click favourite slots {a} and {b} in the live client.")
    result = get_price_diff(a, b, in_shop=False, verbose=True)
    if result is None:
        print("RESULT: None - the difference could not be measured.")
        raise SystemExit(1)
    print(f"RESULT: {result:,}")
