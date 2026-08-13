"""Is the Trade window open, which tab is showing, and switching between them.

Spec: shop.md

Everything here answers a question by LOOKING, never by remembering. A cached
"the window is open" is worth nothing: the window can be closed by the player,
by a disconnect, or by the game itself, and a click sent at a coordinate with
no window under it is not a no-op -- it is a move order into the 3D world that
walks the character away.
"""

from __future__ import annotations

from PIL import Image

from . import geometry as geo
from . import ocr
from . import screen
from .layout import Layout


class ShopClosed(RuntimeError):
    """The Trade window is not open and this flow cannot open it."""


def _words_in(layout: Layout, shot: Image.Image, region) -> list:
    """OCR one region of the Trade window, upscaled for this layout's size."""
    box = layout.cropped(region)
    # NORMALISED TO REFERENCE GLYPH SIZE. The region already shrank with the
    # UI, so a fixed multiplier hands Tesseract smaller letters at 1080p than
    # the confidence thresholds were tuned against. Dividing by the layout
    # scale keeps the final glyph height constant at any resolution.
    upscale = max(1.0, 2.0 / max(0.2, layout.scale))
    return ocr.find_words(shot, box, upscale=upscale, min_conf=20.0)


def _line_tolerance(layout: Layout) -> int:
    """Line-grouping distance in SCREEN pixels for this layout."""
    return max(4, layout.length(10))


def trade_window_open(layout: Layout,
                      source: "Image.Image | None" = None) -> bool:
    """True when the Trade window is on screen."""
    shot = source if source is not None else screen.grab()
    words = _words_in(layout, shot, geo.TRADE_REGION)
    tolerance = _line_tolerance(layout)
    return all(ocr.find_phrase(words, marker, tolerance) is not None
               for marker in geo.TRADE_WINDOW_MARKERS)


def purchase_tab_open(layout: Layout,
                      source: "Image.Image | None" = None) -> bool:
    """True when the window is showing the PURCHASE tab.

    BOTH markers are required. The two tabs share one window, so a single word
    that happens to appear on either proves nothing -- and clicking a
    Purchase-tab coordinate while Register is showing hits the listings table
    instead of the search controls.
    """
    shot = source if source is not None else screen.grab()
    words = _words_in(layout, shot, geo.TRADE_REGION)
    tolerance = _line_tolerance(layout)
    return all(ocr.find_phrase(words, marker, tolerance) is not None
               for marker in geo.PURCHASE_TAB_MARKERS)


def register_tab_open(layout: Layout,
                      source: "Image.Image | None" = None) -> bool:
    """True when the window is showing the REGISTER tab."""
    shot = source if source is not None else screen.grab()
    words = _words_in(layout, shot, geo.TRADE_REGION)
    tolerance = _line_tolerance(layout)
    return all(ocr.find_phrase(words, marker, tolerance) is not None
               for marker in geo.REGISTER_TAB_MARKERS)


def open_purchase_tab(layout: Layout, timeout: float = 10.0,
                      verbose: bool = True) -> bool:
    """Put the window on the Purchase tab. True when it is showing.

    The OUTCOME is what is trusted, not the click. The tab is fixed furniture
    at a known coordinate, so a wrong point costs a timeout rather than a wrong
    action: its only neighbour is the Register tab, and being on the Register
    tab is the state this function was called to leave.
    """
    def say(message: str) -> None:
        if verbose:
            print(message)

    if purchase_tab_open(layout):
        say("  the Purchase tab is already open.")
        return True
    if not trade_window_open(layout):
        say("  the Trade window is not open - refusing to click, the game "
            "world is underneath.")
        return False
    point = layout.point(geo.PURCHASE_TAB)
    say(f"  switching to the Purchase tab at {point}")
    screen.focus_game()
    screen.click(*point)
    ok = screen.wait_until(lambda: purchase_tab_open(layout), timeout=timeout)
    say("  the Purchase tab is open." if ok
        else "  the Purchase tab did not open.")
    return ok


def open_register_tab(layout: Layout, timeout: float = 10.0,
                      verbose: bool = True) -> bool:
    """Put the window back on the Register tab. True when it is showing."""
    def say(message: str) -> None:
        if verbose:
            print(message)

    if register_tab_open(layout):
        return True
    if not trade_window_open(layout):
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

    So it is a named gap rather than a guess. It needs its own spec covering
    the panel's origin detection, the tab strip, the slot grid, and a way to
    confirm what is under the cursor BEFORE pressing anything. Until then this
    flow requires the shop to be open already and says so.
    """
    if verbose:
        print("  open_agent_shop is not implemented: it would have to "
              "right-click an inventory slot, and a wrong slot USES the item "
              "in it. Open the Agent Shop by hand and call again with "
              "in_shop=True.")
    return False
