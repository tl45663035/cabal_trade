"""Find a scroll point that moves the table WITHOUT popping an item tooltip.

THIS ONE DRIVES THE GAME. It sends real wheel notches and moves the cursor.
It buys nothing, cancels nothing and clicks nothing -- wheel and cursor moves
only -- but it must not be run while trade.py is running, and the Agent Shop
must be open on the Register tab with at least a few listings.

    py unit_tests/scroll_point_probe.py

WHY
---
scroll_wheel has to put the cursor over the scrollable area, because Windows
delivers MOUSEEVENTF_WHEEL to whatever window is under the pointer. Today it
uses the CENTRE of TRADE_REGION, which is a listing row -- so every scroll
hovers a listing, which pops the Item Information panel across the middle of
the table. On 2026-08-10 that produced 0-row reads, two 66s "the table could
not be read after scrolling" refusals, and a blocked restock.

The current workaround is TOOLTIP_CLEAR_SECONDS: park, then wait for the game
to take the tooltip down. Better would be a point that is inside the
scrollable control but NOT on a row, so the tooltip never appears -- the table
header or the scrollbar. Whether the game routes the wheel to the row list
from those points is a question only the game can answer, hence this probe.

WHAT IT REPORTS, per candidate:
    scrolled  did the visible row set change (did the wheel reach the list)
    clean     did read_rows come back with a full screen (no tooltip covering)
A candidate that is both is strictly better than the status quo.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade as m  # noqa: E402


def fingerprint(rows):
    """What the screen is showing, as something comparable."""
    return tuple((r.index, r.name, r.qty, r.price) for r in rows)


def live(rows):
    return sum(1 for r in rows if r.name != "(empty)")


def probe(label, point, notches=-1):
    """Scroll at `point`, then report whether it moved and whether it read."""
    m.park_cursor()
    time.sleep(0.4)
    before = m.await_rows(8.0)
    if not before:
        return f"{label:22} SKIP  - the table did not read before scrolling"

    m.scroll_wheel(point[0], point[1], notches)
    # Read WITHOUT parking: this is the whole question. If the tooltip is up,
    # it is up now, and parking first would hide the very thing being measured.
    time.sleep(0.4)
    during = m.read_rows(m.grab())

    m.park_cursor()
    time.sleep(m.TOOLTIP_CLEAR_SECONDS)
    after = m.await_rows(8.0)

    scrolled = bool(after) and fingerprint(before) != fingerprint(after)
    clean = bool(during) and live(during) > 0
    # Put the view back so the next candidate starts from the same place.
    if scrolled:
        m.scroll_wheel(point[0], point[1], -notches)
        m.park_cursor()
        time.sleep(m.TOOLTIP_CLEAR_SECONDS)

    return (f"{label:22} scrolled={'YES' if scrolled else 'no ':3}  "
            f"clean={'YES' if clean else 'no ':3}  "
            f"(rows during scroll: {len(during)}, live {live(during)})")


def main():
    if not m.require_calibration(verbose=True):
        print("Calibration failed -- is the Trade window open?")
        return 1
    if not m.trade_window_open():
        print("The Trade window is not open. Open the Agent Shop first.")
        return 1

    left, top, right, bottom = m.TRADE_REGION
    candidates = [
        ("row centre (current)", ((left + right) // 2, (top + bottom) // 2)),
        ("column header", ((left + right) // 2, top + 117)),
        ("scrollbar", (right - 35, (top + bottom) // 2)),
        ("left edge gutter", (left + 6, (top + bottom) // 2)),
    ]

    print(f"TRADE_REGION {m.TRADE_REGION}")
    print("Probing scroll points. Each sends one wheel notch and puts it back.\n")
    for label, point in candidates:
        try:
            print(" ", probe(label, point), f" at {point}")
        except Exception as exc:  # noqa: BLE001 - a probe must not stop the rest
            print(f"  {label:22} ERROR {type(exc).__name__}: {exc}")

    print("\nPick the first candidate that is scrolled=YES and clean=YES.")
    print("That point pops no tooltip, so TOOLTIP_CLEAR_SECONDS can go.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
