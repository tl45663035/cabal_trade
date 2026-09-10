import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import os
import tempfile
os.environ.setdefault("CABAL_SALES_DB",
                      str(Path(tempfile.gettempdir()) / "gpd_bench.db"))

from PIL import Image, ImageDraw



PAIRS = [
    ("Chaos", 4, 3,
     [("Chaos Core Set X 10", 1, 7_400_000),
      ("Chaos Core Set X 148", 1, 109_628_780),
      ("Chaos Core Set X 250", 2, 185_500_000),
      ("Chaos Core Set X 500", 1, 371_000_000),
      ("Chaos Core Set X 999", 1, 745_000_000)],
     [("Chaos Core", 250, 696_000),
      ("Chaos Core", 120, 698_000),
      ("Chaos Core", 80, 699_500)]),

    ("FCHH", 2, 1,
     [("Force Core Set (Highest) X 10", 1, 2_950_000),
      ("Force Core Set (Highest) X 50", 1, 14_600_000),
      ("Force Core Set (Highest) X 200", 1, 58_000_000)],
     [("Force Core(Highest)", 250, 247_485),
      ("Force Core(Highest)", 100, 249_000)]),

    ("FCU", 6, 5,
     [("Force Core Set (Ultimate) X 10", 1, 5_900_000),
      ("Force Core Set (Ultimate) X 100", 1, 58_500_000)],
     [("Force Core (Ultimate)", 250, 575_000),
      ("Force Core (Ultimate)", 60, 578_000)]),

    ("FCH", 8, 7,
     [("Force Core Set (High) X 10", 1, 1_766_660),
      ("Force Core Set (High) X 100", 1, 17_600_000)],
     [("Force Core(High)", 250, 196_753),
      ("Force Core(High)", 90, 198_000)]),

    ("UCU", 10, 9,
     [("Upgrade Core Set (Ultimate) X 10", 1, 5_100_000),
      ("Upgrade Core Set (Ultimate) X 250", 1, 126_000_000)],
     [("Upgrade Core (Ultimate)", 250, 499_999),
      ("Upgrade Core (Ultimate)", 150, 502_000)]),
]


def render(rows, size=(2560, 1440)):
    img = Image.new("RGB", size, (16, 16, 20))
    d = ImageDraw.Draw(img)

    def at(x, y, text, centre=False):
        if centre:
            x -= d.textlength(text) / 2
        d.text((x, y - 7), text, fill=(236, 236, 236))

    at(608, 19, "Trade", centre=True)
    at(128, 67, "Purchase", centre=True)
    at(382, 69, "Register", centre=True)
    at(919, 65, "Adjust fee : VANGUARD", centre=True)
    at(300, 118, "Category", centre=True)
    at(492, 119, "Name", centre=True)
    at(1010, 119, "Status", centre=True)
    at(1126, 118, "Function", centre=True)
    at(142, 122, "Item", centre=True)
    at(950, 195, "By Price:Low to High", centre=True)
    at(55, 869, "Period", centre=True)
    at(331, 982, "Selling", centre=True)
    at(503, 982, "Expired", centre=True)
    at(674, 980, "Sold", centre=True)
    at(863, 980, "Total", centre=True)
    at(1119, 981, "Refresh", centre=True)

    for i, (name, qty, price) in enumerate(rows):
        y = 340 + i * 76
        at(280, y, name)
        at(790, y, str(qty), centre=True)
        at(990, y, f"{price:,}", centre=True)
    return img


FRAMES = {}
for _name, _set_slot, _core_slot, _set_rows, _core_rows in PAIRS:
    FRAMES[_set_slot] = render(_set_rows)
    FRAMES[_core_slot] = render(_core_rows)



class Meter:
    def __init__(self, name):
        self.name = name
        self.reads = 0
        self.ocr_seconds = 0.0
        self.sleeps = 0
        self.slept = 0.0
        self.pixels = 0

    def report(self):
        return (f"  {self.name:<28} {self.reads:>4} reads  "
                f"{self.ocr_seconds:>6.2f}s OCR   "
                f"{self.sleeps:>3} sleeps {self.slept:>6.2f}s   "
                f"{self.ocr_seconds + self.slept:>6.2f}s total")


def bench_new(slot_a, slot_b):
    from cabal import ocr, purchase, screen, shop
    import get_price_diff as gpd
    from cabal.layout import Layout

    meter = Meter("NEW  src/get_price_diff")
    real_find_words = ocr.find_words

    def counted(image, region, upscale=1.0, min_conf=0.0):
        meter.reads += 1
        w = max(0, int(region[2]) - int(region[0]))
        h = max(0, int(region[3]) - int(region[1]))
        meter.pixels += int(w * h * max(1.0, upscale) ** 2)
        t = time.perf_counter()
        try:
            return real_find_words(image, region, upscale, min_conf)
        finally:
            meter.ocr_seconds += time.perf_counter() - t

    def sleeper(seconds):
        meter.sleeps += 1
        meter.slept += seconds

    frames = {"current": FRAMES[slot_a]}

    saved = {}
    for mod, name, fn in (
            (ocr, "find_words", counted),
            (screen, "grab", lambda: frames["current"]),
            (screen, "focus_game", lambda *a, **k: True),
            (screen, "move_mouse", lambda *a, **k: True),
            (screen, "click", lambda *a, **k: True),
            (screen, "client_rect", lambda: (0, 23, 2560, 1392)),
            (screen, "screen_size", lambda: (2560, 1440)),
            (purchase, "time", type("T", (), {"sleep": staticmethod(sleeper),
                                              "monotonic": time.monotonic})),
    ):
        saved[(mod, name)] = getattr(mod, name)
        setattr(mod, name, fn)

    real_search = purchase.run_favourite_search

    def searching(layout, slot, *a, **k):
        frames["current"] = FRAMES.get(slot, FRAMES[slot_a])
        return real_search(layout, slot, *a, **k)

    saved[(purchase, "run_favourite_search")] = real_search
    purchase.run_favourite_search = searching
    saved[(gpd.purchase, "run_favourite_search")] = real_search

    try:
        gpd.reset_layout()
        wall = time.perf_counter()
        result = gpd.get_price_diff(slot_a, slot_b, in_shop=True, verbose=False)
        wall = time.perf_counter() - wall
    finally:
        for (mod, name), fn in saved.items():
            setattr(mod, name, fn)
    return meter, result, wall


def bench_old(slot_a, slot_b):
    import trade

    meter = Meter("OLD  trade.chaos_margin_now")
    real_find_words = trade.find_words

    def counted(image, region, upscale=None, *a, **k):
        meter.reads += 1
        w = max(0, int(region[2]) - int(region[0]))
        h = max(0, int(region[3]) - int(region[1]))
        meter.pixels += int(w * h)
        t = time.perf_counter()
        try:
            return real_find_words(image, region, upscale, *a, **k)
        finally:
            meter.ocr_seconds += time.perf_counter() - t

    def sleeper(seconds):
        meter.sleeps += 1
        meter.slept += seconds

    frames = {"current": FRAMES[slot_a]}
    real_sleep = time.sleep

    saved = {}
    for name, fn in (
            ("find_words", counted),
            ("grab", lambda: frames["current"]),
            ("focus_game", lambda *a, **k: True),
            ("move_mouse", lambda *a, **k: True),
            ("click", lambda *a, **k: True),
            ("park_cursor", lambda *a, **k: None),
            ("cooldown", lambda *a, **k: None),
            ("wait_for_table", lambda *a, **k: True),
            ("panel_covers_trade_area", lambda *a, **k: True),
            ("client_rect", lambda: (0, 23, 2560, 1392)),
            ("current_screen_size", lambda: (2560, 1440)),
    ):
        saved[name] = getattr(trade, name)
        setattr(trade, name, fn)

    real_search = trade.run_favourite_search

    def searching(slot, *a, **k):
        frames["current"] = FRAMES.get(slot, FRAMES[slot_a])
        return real_search(slot, *a, **k)

    saved["run_favourite_search"] = real_search
    trade.run_favourite_search = searching
    time.sleep = sleeper

    try:
        trade.CHAOS_SET_SLOT = slot_a
        trade.CHAOS_CORE_SLOT = slot_b
        wall = time.perf_counter()
        result = trade.chaos_margin_now(verbose=False)
        wall = time.perf_counter() - wall
    finally:
        time.sleep = real_sleep
        for name, fn in saved.items():
            setattr(trade, name, fn)
    return meter, result, wall


REPEATS = 5


def main():
    print("=" * 86)
    print(f"All {len(PAIRS)} Core/Set pairs, {REPEATS} repeats each, both "
          f"implementations")
    print("=" * 86)

    rows = []
    for name, set_slot, core_slot, _, _ in PAIRS:
        old_runs, new_runs = [], []
        old_answer = new_answer = None
        for _ in range(REPEATS):
            m, r, _w = bench_old(set_slot, core_slot)
            old_runs.append(m); old_answer = r
            m, r, _w = bench_new(set_slot, core_slot)
            new_runs.append(m); new_answer = r

        def avg(runs, field):
            return sum(getattr(m, field) for m in runs) / len(runs)

        rows.append(dict(
            name=name, set_slot=set_slot, core_slot=core_slot,
            old_reads=avg(old_runs, "reads"), new_reads=avg(new_runs, "reads"),
            old_ocr=avg(old_runs, "ocr_seconds"), new_ocr=avg(new_runs, "ocr_seconds"),
            old_sleep=avg(old_runs, "slept"), new_sleep=avg(new_runs, "slept"),
            old_answer=old_answer, new_answer=new_answer))
        print(f"  {name:<6} done")

    print()
    print("SPEED  (mean of %d runs; sleeps excluded -- both wait on the same "
          "server)" % REPEATS)
    print("-" * 86)
    print(f"  {'item':<7}{'OLD reads':>10}{'NEW reads':>10}"
          f"{'OLD OCR':>10}{'NEW OCR':>10}{'faster':>9}{'fewer reads':>13}")
    for r in rows:
        speed = r["old_ocr"] / r["new_ocr"] if r["new_ocr"] else 0
        reads = r["old_reads"] / r["new_reads"] if r["new_reads"] else 0
        print(f"  {r['name']:<7}{r['old_reads']:>10.1f}{r['new_reads']:>10.1f}"
              f"{r['old_ocr']:>9.2f}s{r['new_ocr']:>9.2f}s"
              f"{speed:>8.1f}x{reads:>12.1f}x")
    to = sum(r["old_ocr"] for r in rows); tn = sum(r["new_ocr"] for r in rows)
    ro = sum(r["old_reads"] for r in rows); rn = sum(r["new_reads"] for r in rows)
    print("-" * 86)
    print(f"  {'ALL':<7}{ro:>10.1f}{rn:>10.1f}{to:>9.2f}s{tn:>9.2f}s"
          f"{(to/tn if tn else 0):>8.1f}x{(ro/rn if rn else 0):>12.1f}x")

    print()
    print("PRICE DIFF  (rendered market data, not live quotes -- this is a "
          "cross-check)")
    print("-" * 86)
    print(f"  {'item':<7}{'slots':<10}{'OLD (all rows)':>18}"
          f"{'NEW (row 1)':>16}{'agree':>8}")
    for r in rows:
        agree = "yes" if r["old_answer"] == r["new_answer"] else "NO"
        o = f"{r['old_answer']:,}" if r["old_answer"] is not None else "None"
        n = f"{r['new_answer']:,}" if r["new_answer"] is not None else "None"
        print(f"  {r['name']:<7}{f'{r[chr(39)+chr(39)]}' if False else f'{r["set_slot"]}-{r["core_slot"]}':<10}"
              f"{o:>18}{n:>16}{agree:>8}")

    print()
    print("  The two answer different questions by design. OLD scans EVERY row")
    print("  of the Set search and takes the cheapest per unit; NEW takes row 1")
    print("  by rule. They agree only when row 1 is also the cheapest per unit,")
    print("  which under a sort by LISTING TOTAL is not the common case.")
    print()
    print("  Sleeps per call (unchanged by any of this): "
          f"OLD {rows[0]['old_sleep']:.1f}s  NEW {rows[0]['new_sleep']:.1f}s")


if __name__ == "__main__":
    main()
