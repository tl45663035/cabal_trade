"""Where trade.chaos_margin_now spends its time, live.

DRIVES THE GAME: it runs the real margin gate against the real market. It
buys nothing -- the gate only searches and reads.

Prints every OCR launch grouped by region, plus the sleeps, so the next
change is aimed at the thing that actually costs rather than the thing that
looks expensive.

    python unit_tests/trade_margin_profile.py [item ...]
"""
import collections
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "trade_margin_profile.db")

PAIRS = [("Chaos", 4, 3), ("FCH", 8, 7), ("FCHH", 2, 1),
         ("FCU", 6, 5), ("UCU", 10, 9)]


def main() -> int:
    import trade

    # OPEN FIRST, CALIBRATE SECOND: calibration measures the Trade WINDOW.
    trade.PREMIUM_ENABLED = True
    if not trade.ensure_shop_ready(verbose=True):
        print("Could not open the Agent Shop.")
        return 2
    if not trade.calibrate(verbose=True):
        print("Could not calibrate with the shop open.")
        return 2
    if not trade.purchase_ready(verbose=True):
        if not trade.open_purchase_tab(verbose=True):
            print("Could not reach the Purchase tab.")
            return 2
        if not trade.set_purchase_sort_low_to_high(verbose=True):
            print("Could not confirm the sort.")
            return 2

    by_region = collections.defaultdict(lambda: [0, 0.0])
    sleeps = [0, 0.0]

    real_find_words = trade.find_words
    real_sleep = time.sleep

    def counted(source, region, *a, **k):
        key = tuple(int(v) for v in region) if region else ("full",)
        t = time.perf_counter()
        try:
            return real_find_words(source, region, *a, **k)
        finally:
            took = time.perf_counter() - t
            by_region[key][0] += 1
            by_region[key][1] += took

    def timed_sleep(seconds):
        sleeps[0] += 1
        sleeps[1] += seconds
        real_sleep(seconds)

    trade.find_words = counted
    time.sleep = timed_sleep
    walls = {}
    try:
        for name, set_slot, core_slot in PAIRS:
            trade.CHAOS_SET_SLOT = set_slot
            trade.CHAOS_CORE_SLOT = core_slot
            t = time.perf_counter()
            got = trade.chaos_margin_now(verbose=False)
            walls[name] = time.perf_counter() - t
            print(f"  {name:<6} {walls[name]:6.1f}s -> "
                  f"{got if got is not None else 'None'}")
    finally:
        trade.find_words = real_find_words
        time.sleep = real_sleep

    total_wall = sum(walls.values())
    total_ocr = sum(v[1] for v in by_region.values())
    total_reads = sum(v[0] for v in by_region.values())

    print()
    print("=" * 82)
    print(f"{len(PAIRS)} calls, {total_wall:.1f}s wall")
    print("=" * 82)
    print(f"  {'OCR launches':<34}{total_reads:>8}"
          f"{total_ocr:>9.1f}s{total_ocr / total_wall * 100:>7.0f}%")
    print(f"  {'sleeping':<34}{sleeps[0]:>8}"
          f"{sleeps[1]:>9.1f}s{sleeps[1] / total_wall * 100:>7.0f}%")
    other = total_wall - total_ocr - sleeps[1]
    print(f"  {'everything else':<34}{'':>8}"
          f"{other:>9.1f}s{other / total_wall * 100:>7.0f}%")

    print()
    print("BY REGION  (the crop each launch was given)")
    print("-" * 82)
    print(f"  {'region':<34}{'launches':>9}{'seconds':>10}"
          f"{'each':>9}{'share':>8}")
    for key, (n, secs) in sorted(by_region.items(), key=lambda kv: -kv[1][1]):
        if len(key) == 4:
            w, h = key[2] - key[0], key[3] - key[1]
            label = f"{key}  {w}x{h}"
        else:
            label = str(key)
        print(f"  {label:<34}{n:>9}{secs:>9.1f}s"
              f"{secs / n * 1000:>8.0f}ms{secs / total_ocr * 100:>7.0f}%")
    print()
    print(f"  per call: {total_reads / len(PAIRS):.0f} launches, "
          f"{total_ocr / len(PAIRS):.1f}s OCR, "
          f"{sleeps[1] / len(PAIRS):.1f}s sleeping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
