import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade as m


def fingerprint(rows):
    return tuple((r.index, r.name, r.qty, r.price) for r in rows)


def live(rows):
    return sum(1 for r in rows if r.name != "(empty)")


def probe(label, point, notches=-1):
    m.park_cursor()
    time.sleep(0.4)
    before = m.await_rows(8.0)
    if not before:
        return f"{label:22} SKIP  - the table did not read before scrolling"

    m.scroll_wheel(point[0], point[1], notches)
    time.sleep(0.4)
    during = m.read_rows(m.grab())

    m.park_cursor()
    time.sleep(m.TOOLTIP_CLEAR_SECONDS)
    after = m.await_rows(8.0)

    scrolled = bool(after) and fingerprint(before) != fingerprint(after)
    clean = bool(during) and live(during) > 0
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
        except Exception as exc:
            print(f"  {label:22} ERROR {type(exc).__name__}: {exc}")

    print("\nPick the first candidate that is scrolled=YES and clean=YES.")
    print("That point pops no tooltip, so TOOLTIP_CLEAR_SECONDS can go.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
