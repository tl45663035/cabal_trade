import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade as m


def walk(label, rows_wanted, calibrated):
    steps = []
    real_say = None

    if calibrated:
        m.calibrate_scroll(timeout=8.0, verbose=True)
    else:
        m.forget_scroll_calibration()

    t0 = time.monotonic()
    found = m.enumerate_listings(timeout=8.0, verbose=False,
                                 stop_after=rows_wanted)
    elapsed = time.monotonic() - t0
    n = len(found) if found else 0
    print(f"  {label:34} {elapsed:7.1f}s   {n:>3} row(s) read")
    return elapsed, n, steps


def main():
    rows_wanted = int(sys.argv[1]) if len(sys.argv) > 1 else 25

    if not m.ensure_calibrated(verbose=True):
        print("Calibration failed - is the Trade window open?")
        return 1
    if not m.trade_window_open():
        print("The Trade window is not open; opening it from the shop key.")
        m.PREMIUM_ENABLED = True
        if not m.ensure_shop_ready(verbose=True) or not m.trade_window_open():
            print("Could not open the Agent Shop.")
            return 1

    print(f"\nProbing the wheel against rows 1-{rows_wanted}. "
          f"Scrolling only -- nothing is clicked or bought.\n")

    m.forget_scroll_calibration()
    ratio = m.calibrate_scroll(timeout=8.0, verbose=True)
    if ratio is None:
        print("  the wheel could not be calibrated -- either the table does "
              "not scroll here, or one notch could not be measured.")
        print("  the sweep will fall back to measuring every step, which is "
              "the behaviour this probe exists to compare against.")
    else:
        print(f"  MEASURED: one notch = {ratio:g} row(s)")
        print(f"  SCROLL_STEP is {m.SCROLL_STEP}, so a full stride is "
              f"{m.SCROLL_STEP / ratio:.0f} notch(es)")

    print("\nwalking the same rows twice:")
    off_s, off_n, _ = walk("measuring every step (old)", rows_wanted, False)
    on_s, on_n, _ = walk("calibrated stride (new)", rows_wanted, True)

    print()
    if off_n != on_n:
        print(f"  ** the two walks disagree: {off_n} rows vs {on_n}. The "
              f"calibrated stride must read the SAME rows, not fewer -- "
              f"treat this as a failure, not a speed-up.")
    elif off_s > 0:
        print(f"  same {on_n} rows both ways; calibrated is "
              f"{off_s / on_s:.2f}x the speed "
              f"({off_s:.1f}s -> {on_s:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
