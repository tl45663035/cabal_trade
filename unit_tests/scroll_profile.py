"""Where every millisecond of scrolling and row access goes.

DRIVES THE GAME. Scrolls the Register table and reads it. Clicks nothing in
the table, buys nothing, lists nothing.

Every primitive is wrapped and timed: the wheel notch by notch, the cursor
park, the screenshot, each OCR launch by region, and the row reads. Then the
three ways of reaching a row are run and broken down against those totals.

    python unit_tests/scroll_profile.py
"""
import collections
import functools
import os
import sys
import tempfile
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "scroll_profile.db")


class Clock:
    def __init__(self):
        self.calls = collections.defaultdict(lambda: [0, 0.0])
        self.regions = collections.defaultdict(lambda: [0, 0.0])
        self.notches = 0

    def wrap(self, module, name, label=None):
        real = getattr(module, name)
        key = label or name

        @functools.wraps(real)
        def timed(*a, **k):
            t = time.perf_counter()
            try:
                return real(*a, **k)
            finally:
                took = time.perf_counter() - t
                self.calls[key][0] += 1
                self.calls[key][1] += took
        setattr(module, name, timed)
        return real

    def wrap_ocr(self, module):
        real = module.find_words

        @functools.wraps(real)
        def timed(image, region, *a, **k):
            key = tuple(int(v) for v in region) if region else ("full",)
            t = time.perf_counter()
            try:
                return real(image, region, *a, **k)
            finally:
                took = time.perf_counter() - t
                self.calls["find_words"][0] += 1
                self.calls["find_words"][1] += took
                self.regions[key][0] += 1
                self.regions[key][1] += took
        module.find_words = timed
        return real

    def wrap_wheel(self, module):
        real = module.scroll_wheel

        @functools.wraps(real)
        def timed(x, y, notches, *a, **k):
            self.notches += abs(int(notches))
            t = time.perf_counter()
            try:
                return real(x, y, notches, *a, **k)
            finally:
                self.calls["scroll_wheel"][0] += 1
                self.calls["scroll_wheel"][1] += time.perf_counter() - t
        module.scroll_wheel = timed
        return real

    def reset(self):
        self.calls.clear()
        self.regions.clear()
        self.notches = 0

    def report(self, title, wall):
        print()
        print(f"  {title} -- {wall:.2f}s wall")
        print(f"  {'call':<26}{'n':>5}{'total':>10}{'each':>10}{'share':>8}")
        for key, (n, secs) in sorted(self.calls.items(), key=lambda kv: -kv[1][1]):
            print(f"    {key:<24}{n:>5}{secs:>9.2f}s"
                  f"{secs / n * 1000:>9.0f}ms{secs / wall * 100:>7.0f}%")
        if self.notches:
            wheel = self.calls.get("scroll_wheel", [0, 0.0])[1]
            print(f"    {'-> wheel notches':<24}{self.notches:>5}"
                  f"{wheel:>9.2f}s{wheel / self.notches * 1000:>9.0f}ms"
                  f"{'  per notch':>9}")
        if self.regions:
            print(f"    OCR by region:")
            for key, (n, secs) in sorted(self.regions.items(),
                                         key=lambda kv: -kv[1][1]):
                if len(key) == 4:
                    label = f"{key[2]-key[0]}x{key[3]-key[1]}"
                else:
                    label = str(key)
                print(f"      {label:<22}{n:>5}{secs:>9.2f}s"
                      f"{secs / n * 1000:>9.0f}ms")


def main() -> int:
    import trade
    print(__doc__)
    trade.PREMIUM_ENABLED = True

    if not trade.ensure_shop_ready(verbose=False):
        print("Could not open the Agent Shop.")
        return 2
    if not trade.open_trade_window(verbose=False):
        print("Could not reach the Register tab.")
        return 2
    if not trade.calibrate(verbose=False):
        print("Could not calibrate.")
        return 2
    trade.calibrate_scroll(verbose=False)
    print(f"  calibrated, {trade.scroll_rows_per_notch()} row(s) per notch")
    print(f"  SCROLL_TO_END_NOTCHES = {trade.SCROLL_TO_END_NOTCHES}")
    print(f"  wheel settle          = 0.35s a notch (scroll_wheel default)")
    print(f"  TOOLTIP_CLEAR_SECONDS = {trade.TOOLTIP_CLEAR_SECONDS}")
    print(f"  ACTION_COOLDOWN       = {trade.ACTION_COOLDOWN}")

    clock = Clock()
    for name in ("grab", "park_cursor", "move_mouse", "read_top_row",
                 "read_rows", "await_rows", "scroll_to_end", "table_scrollable",
                 "find_row_buttons", "scroll_one", "measure_shift",
                 "anchor_shift", "panel_covers_trade_area", "table_loading"):
        if hasattr(trade, name):
            clock.wrap(trade, name)
    clock.wrap_ocr(trade)
    clock.wrap_wheel(trade)

    print()
    print("=" * 84)
    print("A. ONE NOTCH, in isolation")
    print("=" * 84)
    clock.reset()
    centre = ((trade.TRADE_REGION[0] + trade.TRADE_REGION[2]) // 2,
              (trade.TRADE_REGION[1] + trade.TRADE_REGION[3]) // 2)
    t = time.perf_counter()
    trade.scroll_wheel(centre[0], centre[1], -1)
    clock.report("scroll_wheel(-1)", time.perf_counter() - t)

    print()
    print("=" * 84)
    print("B. scroll_to_end(up) -- what goto_row pays before it starts")
    print("=" * 84)
    clock.reset()
    t = time.perf_counter()
    trade.scroll_to_end(up=True, verbose=False)
    clock.report("scroll_to_end(up=True)", time.perf_counter() - t)

    print()
    print("=" * 84)
    print("C. read_top_row -- the header band alone")
    print("=" * 84)
    clock.reset()
    t = time.perf_counter()
    trade.read_top_row()
    clock.report("read_top_row()", time.perf_counter() - t)

    print()
    print("=" * 84)
    print("D. await_rows -- the whole visible table, for comparison")
    print("=" * 84)
    clock.reset()
    t = time.perf_counter()
    trade.await_rows(8.0)
    clock.report("await_rows()", time.perf_counter() - t)

    print()
    print("=" * 84)
    print("E. one STEP: a notch, a park, and a top-row read")
    print("=" * 84)
    clock.reset()
    t = time.perf_counter()
    trade.scroll_wheel(*trade.SCROLL_POINT, -1, checked=True)
    trade.read_top_row()
    clock.report("one notch + top-row read", time.perf_counter() - t)

    print()
    print("=" * 84)
    print("F. goto_row(12) -- the whole thing")
    print("=" * 84)
    clock.reset()
    t = time.perf_counter()
    trade.goto_row(12, verbose=False)
    clock.report("goto_row(12)", time.perf_counter() - t)

    print()
    print("=" * 84)
    print("G. shop_listing_pairs -- the full walk this is meant to replace")
    print("=" * 84)
    clock.reset()
    t = time.perf_counter()
    pairs = trade.shop_listing_pairs(timeout=8.0, verbose=False)
    walk = time.perf_counter() - t
    clock.report(f"full walk ({len(pairs or [])} rows)", walk)

    trade.leave_shop(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
