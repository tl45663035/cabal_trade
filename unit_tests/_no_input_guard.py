"""Run a test file with every game-input path armed to raise.

Purpose: prove a test cannot drive the game, and name it if it tries.

On 2026-08-12 chaos_priority_test called the REAL restock_core to exercise its
row-capacity gate. That is legitimate for the cases where the gate REFUSES --
it returns before doing anything. The cases where the gate ALLOWS fall straight
through into buy_sets_until, the Purchase tab, and real clicks against a live
client with real Alz behind them. The operator saw their screen being driven.

`_send` and `_release` are the only two functions in trade.py that reach
SendInput, so patching them closes every path -- but they are patched LAST, as
a backstop. The named primitives are patched first so the failure names the
thing the test actually called rather than the syscall underneath it.

    python unit_tests/_no_input_guard.py unit_tests/some_test.py
"""
import os
import sys
import tempfile
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# setdefault: a test that already pointed at its own scratch ledger keeps it.
# Overriding would move the ledger out from under assertions that expect it.
os.environ.setdefault(
    "CABAL_SALES_DB",
    str(Path(tempfile.mkdtemp(prefix="cabal_guard_")) / "scratch.db"))
os.environ.setdefault("CABAL_NO_RECORD", "1")

import trade as m  # noqa: E402

TRIPPED = []

# Every function that can move the mouse, press a key or turn the wheel.
PRIMITIVES = [
    "click", "ctrl_click", "right_click", "move_mouse", "move_mouse_to_alz",
    "press_key", "press_escape", "type_number", "scroll_wheel", "park_cursor",
    # the two that actually call SendInput -- the backstop
    "_send", "_release",
    "alt_click", "release_modifiers", "open_inventory",
]


def _arm(name):
    def raiser(*a, **k):
        TRIPPED.append(name)
        raise AssertionError(
            f"GAME INPUT from a test: {name}({', '.join(map(repr, a))[:70]}). "
            f"Unit tests must not drive the client.")
    return raiser


for _n in PRIMITIVES:
    if hasattr(m, _n):
        setattr(m, _n, _arm(_n))


# focus_game IS NOT ARMED -- it is NEUTERED.
#
# It touches the client before any armed primitive is reached: ShowWindow
# un-minimises Cabal, SetForegroundWindow steals focus, and when that is
# refused (the normal case with a terminal focused) it injects a real Alt
# keydown. Raising here would leave those first two already done, and dozens of
# call sites `require(focus_game(), ...)` -- so it returns True and does
# nothing, which is the honest answer for "there is no window to focus".
def _no_focus(*a, **k):
    TRIPPED.append("focus_game(neutered)")
    return True


if hasattr(m, "focus_game"):
    m.focus_game = _no_focus

# SCREEN CAPTURE IS BLANKED, NOT BLOCKED.
#
# grab() is read-only -- it does not drive the game -- so raising on it turns
# suites that merely read into crashes. But it DOES hit the live client, which
# is how a "read-only" test becomes a 20-minute hang against a window that is
# not there, and it is what a test walks off the end of just before it starts
# clicking.
#
# So it returns a blank frame of the calibrated size: OCR against it reads
# nothing, which is the honest answer for "there is no game here", and the call
# is still recorded so the audit can name suites that expect a real screen.
def _blank_grab(*a, **k):
    TRIPPED.append("grab(blanked)")
    img = m.Image.new("RGB", (2560, 1440), "black")
    m._last_shot = img
    try:
        m._FRAME_SERIAL += 1
    except Exception:  # noqa: BLE001 - the serial is an optimisation
        pass
    return img


for _n in ("grab", "screenshot", "capture", "grab_frame"):
    if hasattr(m, _n):
        setattr(m, _n, _blank_grab)
# take_screenshot is the layer under grab; block it outright so nothing
# reaches the display server by another route.
if hasattr(m, "take_screenshot"):
    m.take_screenshot = _arm("take_screenshot")

def _report():
    """Always name what was touched, whatever the test did with the error.

    A test that wraps its subject in `except Exception` swallows the raiser and
    reports a failed check instead -- which reads as an ordinary red test and
    hides the fact that, without this guard, it would have driven the client.
    """
    if TRIPPED:
        seen = sorted(set(TRIPPED))
        print(f"\nDRIVES GAME INPUT -> {', '.join(seen)} "
              f"({len(TRIPPED)} call(s))")


# AT MODULE SCOPE, not inside __main__. Registered only in the __main__ block,
# the audit never printed for the suites that IMPORT the guard -- which is all
# of them -- so a swallowed raiser (several suites wrap their subject in
# `except Exception`) read as an ordinary red check with no sign that the
# client would have been driven.
import atexit  # noqa: E402
atexit.register(_report)

if __name__ == "__main__":
    target = Path(sys.argv[1]).resolve()
    # utf-8-sig: several suites carry a BOM, and exec() treats it as a stray
    # non-printable character. Reading them as plain utf-8 made five files
    # report SyntaxError instead of their real verdict.
    src = target.read_text(encoding="utf-8-sig")
    g = {"__name__": "__main__", "__file__": str(target)}
    try:
        exec(compile(src, str(target), "exec"), g)
    except SystemExit as e:
        raise SystemExit(e.code)
    except AssertionError as e:
        if TRIPPED:
            print(f"\nDRIVES GAME INPUT: {target.name} -> {TRIPPED[0]}")
            print(f"  {e}")
            raise SystemExit(2)
        raise
    except Exception:
        if TRIPPED:
            print(f"\nDRIVES GAME INPUT: {target.name} -> {TRIPPED[0]}")
            raise SystemExit(2)
        traceback.print_exc()
        raise SystemExit(3)
