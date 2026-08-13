"""Is the Trade window open, which tab is showing, and switching between them.

Spec: shop.md

Everything here answers a question by LOOKING, never by remembering. A cached
"the window is open" is worth nothing: the window can be closed by the player,
by a disconnect, or by the game itself, and a click sent at a coordinate with
no window under it is not a no-op -- it is a move order into the 3D world that
walks the character away.

LOOKING ONCE, THOUGH. All three state questions are answered from a single OCR
of one band across the top of the window. That is not a weakening of the rule
above: the band is still re-read before every click. It is read once per check
instead of three times, which at ~70ms of process launch per read is most of
the cost of a check.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image

from . import geometry as geo
from . import ocr
from . import screen
from .layout import Layout


class ShopClosed(RuntimeError):
    """The Trade window is not open and this flow cannot open it."""


_SORT_DIRECTION = re.compile(geo.SORT_DIRECTION, re.I)


@dataclass(frozen=True)
class ShopState:
    """Everything the one state read can tell us."""
    window_open: bool
    purchase_tab: bool
    register_tab: bool
    sorted_low_to_high: bool


def state_upscale(layout: Layout) -> float:
    """OCR upscale that keeps the FINAL glyph height constant.

    The region already shrank with the UI, so a fixed multiplier hands
    Tesseract smaller letters at 1080p than the confidence thresholds were
    tuned against, and the reads get quietly worse in a way that reads as a
    coordinate fault.
    """
    return max(1.0, 2.0 / max(0.2, layout.scale))


def line_tolerance(layout: Layout) -> int:
    """Line-grouping distance in SCREEN pixels for this layout."""
    return max(4, layout.length(10))


def read_state(layout: Layout,
               frame: "ocr.Frame | None" = None) -> ShopState:
    """Window, tab and sort markers, from ONE read of the control band.

    Pass a Frame to share the read with other questions about the same
    screenshot; omit it and one is taken.
    """
    frame = frame or ocr.Frame(screen.grab())
    words = frame.words(layout.cropped(geo.STATE_BAND),
                        upscale=state_upscale(layout), min_conf=20.0)
    tolerance = line_tolerance(layout)

    def seen(phrase: str) -> bool:
        return ocr.find_phrase(words, phrase, tolerance) is not None

    # ANY of the markers. See geometry.TRADE_WINDOW_MARKERS: the window's own
    # title does not read reliably, so presence is established from the plain
    # UI text that is always on one tab or the other.
    window = any(seen(m) for m in geo.TRADE_WINDOW_MARKERS)

    # The sort control lives inside the same band, so the direction costs
    # nothing on top of the tab question -- the words are already read. Only
    # the words physically over the control are considered, because the tab
    # labels and column headers in this band contain neither 'low' nor 'high'
    # but a future marker might.
    sort_box = layout.cropped(geo.SORT_REGION)
    over_control = " ".join(
        w.text for w in sorted(words, key=lambda w: w.left)
        if sort_box[0] <= w.centre[0] <= sort_box[2]
        and sort_box[1] <= w.centre[1] <= sort_box[3])
    match = _SORT_DIRECTION.search(over_control)
    # Fails closed: an unread direction, or no 'Price:' at all, is False.
    low_to_high = bool(match) and match.group(1).casefold() == "low"

    return ShopState(
        window_open=window,
        # Both markers per tab, never one: the two tabs share a window, and
        # clicking a Purchase coordinate while Register is showing hits the
        # listings table instead of the search controls.
        purchase_tab=window and all(seen(m) for m in geo.PURCHASE_TAB_MARKERS),
        register_tab=window and all(seen(m) for m in geo.REGISTER_TAB_MARKERS),
        sorted_low_to_high=low_to_high,
    )


# -- the individual questions, for callers that only want one ---------------
#
# Each takes an optional Frame so it costs nothing extra when the caller has
# already read the band.

def trade_window_open(layout: Layout,
                      frame: "ocr.Frame | None" = None) -> bool:
    return read_state(layout, frame).window_open


def purchase_tab_open(layout: Layout,
                      frame: "ocr.Frame | None" = None) -> bool:
    return read_state(layout, frame).purchase_tab


def register_tab_open(layout: Layout,
                      frame: "ocr.Frame | None" = None) -> bool:
    return read_state(layout, frame).register_tab


def sorted_low_to_high(layout: Layout,
                       frame: "ocr.Frame | None" = None) -> bool:
    return read_state(layout, frame).sorted_low_to_high


def open_purchase_tab(layout: Layout, timeout: float = 10.0,
                      verbose: bool = True,
                      frame: "ocr.Frame | None" = None) -> bool:
    """Put the window on the Purchase tab. True when it is showing.

    The OUTCOME is what is trusted, not the click. The tab is fixed furniture
    at a known coordinate, so a wrong point costs a timeout rather than a wrong
    action: its only neighbour is the Register tab, and being on the Register
    tab is the state this function was called to leave.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    state = read_state(layout, frame)
    if state.purchase_tab:
        say("  the Purchase tab is already open.")
        return True
    if not state.window_open:
        say("  the Trade window is not open - refusing to click, the game "
            "world is underneath.")
        return False
    point = layout.point(geo.PURCHASE_TAB)
    say(f"  switching to the Purchase tab at {point}")
    screen.focus_game()
    screen.click(*point)
    # A NEW frame every poll: the whole question is whether the screen has
    # changed, and the frame passed in is by definition from before the click.
    ok = screen.wait_until(lambda: purchase_tab_open(layout), timeout=timeout)
    say("  the Purchase tab is open." if ok
        else "  the Purchase tab did not open.")
    return ok


def open_register_tab(layout: Layout, timeout: float = 10.0,
                      verbose: bool = True,
                      frame: "ocr.Frame | None" = None) -> bool:
    """Put the window back on the Register tab. True when it is showing."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    state = read_state(layout, frame)
    if state.register_tab:
        return True
    if not state.window_open:
        return False
    point = layout.point(geo.REGISTER_TAB)
    say(f"  switching to the Register tab at {point}")
    screen.focus_game()
    screen.click(*point)
    return screen.wait_until(lambda: register_tab_open(layout), timeout=timeout)


def close_shop(layout: Layout, attempts: int = 4,
               verbose: bool = True) -> bool:
    """Close the Trade window with Escape. True when it is gone.

    Escape rather than the window's own close button: the button's position
    moves with the window and the key does not, and this runs at the end of a
    call that has already decided its outcome. Never raises -- a tidy-up that
    throws would replace the caller's result with a crash.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    try:
        screen.focus_game()
        for _ in range(attempts):
            if not trade_window_open(layout):
                say("  Agent Shop closed; the game is back to its default state.")
                return True
            screen.press_escape()
        if trade_window_open(layout):
            say("  Note: the Trade window would not close with Escape - close "
                "it by hand.")
            return False
        say("  Agent Shop closed; the game is back to its default state.")
        return True
    except Exception as exc:  # noqa: BLE001 - tidying must not become the story
        say(f"  Note: could not close the Trade window ({exc}).")
        return False


def open_agent_shop(layout: Layout, verbose: bool = True) -> bool:
    """NOT IMPLEMENTED, deliberately, and it fails closed.

    Opening the shop without walking to the NPC means right-clicking the Agent
    Shop key in the inventory: toggle the panel, switch to the key's tab, then
    right-click a slot in a grid.

    That last step is the problem. A right-click on an inventory slot USES what
    is in it. If the panel geometry is even one slot out -- and the panel is
    anchored to the client's right edge, so it moves with the window rather
    than with the Trade frame -- the click consumes whatever is actually there.
    There is no undo for that, and the failure is silent: the shop simply does
    not open, and something in the bag is gone.

    So it is a named gap rather than a guess. See shop.md.
    """
    if verbose:
        print("  open_agent_shop is not implemented: it would have to "
              "right-click an inventory slot, and a wrong slot USES the item "
              "in it. Open the Agent Shop by hand and call again with "
              "in_shop=True.")
    return False
