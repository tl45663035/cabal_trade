"""A dry run must not touch the game AT ALL -- not merely "not click".

The incident, 2026-08-04 23:14:

    py trade.py --relist-rows all --dry-run

sent 40 wheel notches at the Trade window's centre, because scroll_to_end is
not gated on dry_run and nothing under it was either. The Trade window happened
to be closed, so the wheel went to the game WORLD as a camera zoom. The live run
in progress -- healthy for 48 minutes -- then failed two cycles in a row on
"Lady Yekaterina (Agent Shop) is not on screen" and its breaker stopped it.

Nothing was clicked. That was exactly the problem: `dry_run` was a per-call
argument threaded through the ACTING functions, so "does not click" was true
and "does not touch the game" was not. An argument can be forgotten by a new
caller; a check inside the primitive cannot.

Deliberately does NOT use the Harness for the primitive checks. The harness
REPLACES click/scroll_wheel/type_number with recorders, so a test written
against it proves the recorder sends nothing -- which is true whether or not
the guard exists. The real functions are called here, with only the Windows
call at the very bottom (_send) intercepted.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from harness import check, section, summary  # noqa: E402

import trade  # noqa: E402

# Captured at import, before any harness has installed anything over them.
REAL = {name: getattr(trade, name) for name in
        ("scroll_wheel", "click", "ctrl_click", "press_escape",
         "type_number", "move_mouse")}


class Spy:
    """Intercept the lowest layer only: the actual Windows input call."""

    def __init__(self):
        self.events = []
        self.printed = []
        self._saved = {}

    def __enter__(self):
        for name, fn in (("_send", lambda *a, **k: self.events.append(a)),
                         ("make_dpi_aware", lambda: None),
                         ("cooldown", lambda *a, **k: None),
                         ("print", self.printed.append)):
            self._saved[name] = getattr(trade, name, None)
            setattr(trade, name, fn)
        # SetCursorPos is a real Windows call; stub it so a test can never move
        # a cursor even if the guard is broken.
        self._saved["_real_move"] = trade.move_mouse
        return self

    def __exit__(self, *exc):
        for name, fn in self._saved.items():
            if name == "_real_move":
                continue
            if fn is None:
                delattr(trade, name)
            else:
                setattr(trade, name, fn)
        self._saved = {}
        return False

    def said(self, needle):
        return any(needle.casefold() in str(p).casefold()
                   for p in self.printed)


def with_no_input(flag):
    before = trade.NO_INPUT
    trade.NO_INPUT = flag
    return before


# ===========================================================================
section("every input primitive is silent when NO_INPUT is set")

before = with_no_input(True)
try:
    with Spy() as spy:
        REAL["scroll_wheel"](600, 400, -40)
        check("scroll_wheel sends nothing", not spy.events,
              f"{len(spy.events)} input event(s) -- the wheel is the dangerous "
              f"one: with the Trade window shut it zooms the game camera")
        check("scroll_wheel says what it would have done",
              spy.said("would scroll"), f"{spy.printed}")

    with Spy() as spy:
        REAL["click"](500, 500)
        check("click sends nothing", not spy.events, f"{spy.events}")

    with Spy() as spy:
        REAL["ctrl_click"](500, 500)
        check("ctrl_click sends nothing", not spy.events, f"{spy.events}")

    with Spy() as spy:
        REAL["press_escape"]()
        check("press_escape sends nothing", not spy.events, f"{spy.events}")

    with Spy() as spy:
        REAL["type_number"](9999)
        check("type_number sends nothing", not spy.events, f"{spy.events}")

    with Spy() as spy:
        got = REAL["move_mouse"](100, 200)
        check("move_mouse returns True when suppressed", got is True,
              f"got {got!r} -- False means 'Windows refused', and callers turn "
              f"that into PermissionError('run as Administrator'), which would "
              f"be a lie about why nothing moved")
finally:
    with_no_input(before)


# ===========================================================================
section("with NO_INPUT clear, the primitives DO act")

# Without this the suite would pass just as well against a scroll_wheel that
# never works at all.
before = with_no_input(False)
try:
    with Spy() as spy:
        trade.move_mouse = lambda x, y: True      # no real cursor, ever
        try:
            REAL["scroll_wheel"](600, 400, -3)
        finally:
            trade.move_mouse = REAL["move_mouse"]
        check("scroll_wheel sends one event per notch", len(spy.events) == 3,
              f"{len(spy.events)} event(s) for 3 notches -- suppression has to "
              f"be conditional, or the script can never act at all")

    with Spy() as spy:
        trade.move_mouse = lambda x, y: True
        try:
            REAL["click"](500, 500)
        finally:
            trade.move_mouse = REAL["move_mouse"]
        check("click sends events when not suppressed", len(spy.events) >= 1,
              f"{len(spy.events)} event(s)")
finally:
    with_no_input(before)


# ===========================================================================
section("the sweep, the exact path that caused the incident")

before = with_no_input(True)
try:
    with Spy() as spy:
        # scroll_to_end is what --relist-rows all reaches first, and what sent
        # the 40 notches.
        trade.scroll_to_end(up=True, timeout=0.01, verbose=False)
        check("scroll_to_end sends no input under NO_INPUT", not spy.events,
              f"{len(spy.events)} input event(s) -- this is the call that "
              f"zoomed the camera and ended a live run")
finally:
    with_no_input(before)


# ===========================================================================
section("the flag defaults off and is restored")

check("NO_INPUT is False by default", trade.NO_INPUT is False,
      f"{trade.NO_INPUT!r} -- a module that starts up suppressed would do "
      f"nothing at all, silently, on a real run")
check("the real primitives are still installed",
      trade.scroll_wheel is REAL["scroll_wheel"],
      "this suite must not leave stubs behind for the suites after it")


raise SystemExit(summary())
