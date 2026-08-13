"""get_price_diff: row 1 always, per unit always, None never 0.

DRIVES NOTHING. Every trade.* primitive that would click is replaced before
the module under test can reach it, and the guard below arms the input layer
to raise if anything slips through.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

# Never the real ledger. Set before trade is imported anywhere.
_DB = Path(tempfile.gettempdir()) / "get_price_diff_test.db"
os.environ["CABAL_SALES_DB"] = str(_DB)

import _no_input_guard  # noqa: F401,E402  - arms click/type/screenshot to raise
import trade  # noqa: E402
import get_price_diff as gpd  # noqa: E402

assert str(trade.SALES_DB) == str(_DB), (
    f"refusing to run against the real ledger at {trade.SALES_DB}")

PASS = FAIL = 0


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def offer(row, name, price, pack, available=1):
    return trade.Offer(row=row, name=name, price=price, pack=pack, y=0,
                       available=available)


class Game:
    """Stands in for every trade.* call get_price_diff makes."""

    def __init__(self, results, window_open=True, register=False,
                 purchase_tab=True, sort=True, can_open=True):
        self.results = results          # {slot: [Offer, ...]}
        self.window_open = window_open
        self.register = register
        self.purchase_tab = purchase_tab
        self.sort = sort
        self.can_open = can_open
        self.events = []
        self._saved = {}

    def __enter__(self):
        names = ("trade_window_open", "register_tab_open", "ensure_shop_ready",
                 "open_purchase_tab", "set_purchase_sort_low_to_high",
                 "run_favourite_search", "leave_shop", "open_trade_window")
        for n in names:
            self._saved[n] = getattr(trade, n)
        trade.trade_window_open = lambda *a, **k: self.window_open
        trade.register_tab_open = lambda *a, **k: self.register
        trade.ensure_shop_ready = self._open
        trade.open_purchase_tab = self._purchase
        trade.set_purchase_sort_low_to_high = self._sort
        trade.run_favourite_search = self._search
        trade.leave_shop = self._leave
        trade.open_trade_window = self._reopen
        return self

    def __exit__(self, *exc):
        for n, fn in self._saved.items():
            setattr(trade, n, fn)
        return False

    def _open(self, *a, **k):
        self.events.append("open_shop")
        if self.can_open:
            self.window_open = True
            self.register = True
        return self.can_open

    def _purchase(self, *a, **k):
        self.events.append("purchase_tab")
        if self.purchase_tab:
            self.register = False
        return self.purchase_tab

    def _sort(self, *a, **k):
        self.events.append("sort")
        return self.sort

    def _search(self, slot, *a, **k):
        self.events.append(f"search:{slot}")
        return list(self.results.get(slot, []))

    def _leave(self, *a, **k):
        self.events.append("leave_shop")
        self.window_open = False
        return True

    def _reopen(self, *a, **k):
        self.events.append("restore_register")
        self.register = True
        return True


print("=" * 70)
print("price_per_unit")
print("=" * 70)

check(gpd.price_per_unit(offer(1, "Item X 10", 7_400_000, 10)) == 740_000,
      "a 10-pack at 7,400,000 is 740,000 per unit")
check(gpd.price_per_unit(offer(1, "Item", 694_980, 1)) == 694_980,
      "a pack of 1 is divided too, and is unchanged by it")
check(gpd.price_per_unit(offer(1, "Item X 148", 109_628_780, 148)) == 740_735,
      "the bundle that made raw subtraction wrong reads 740,735 per unit")
check(gpd.price_per_unit(offer(1, "Item", 0, 1)) is None,
      "a price of 0 is unreadable, not free")
check(gpd.price_per_unit(offer(1, "Item", 100, 0)) is None,
      "a pack of 0 is refused rather than treated as 1")
check(gpd.price_per_unit(offer(1, "Item", 100, None)) is None,
      "a pack that did NOT READ is refused -- treating it as 1 would inflate "
      "a 148-bundle's unit price 148x, and in the direction that makes a bad "
      "trade look good")
check(gpd.price_per_unit(None) is None, "no offer at all is None")

print("=" * 70)
print("row 1 is selected by its row number, never by position")
print("=" * 70)

rows = [offer(2, "second", 200, 1), offer(1, "first", 100, 1),
        offer(3, "third", 300, 1)]
check(gpd._row_one(rows).name == "first",
      "row 1 is found even when it is not first in the list -- a filter "
      "upstream must not silently promote another row")
check(gpd._row_one([offer(2, "x", 1, 1)]) is None,
      "when row 1 is absent, NO row is substituted for it")
check(gpd._row_one([]) is None, "an empty result has no row 1")

print("=" * 70)
print("get_price_diff")
print("=" * 70)

A_SET = [offer(1, "Chaos Core Set X 10", 7_400_000, 10),
         offer(2, "Chaos Core Set X 999", 745_000_000, 999)]
B_CORE = [offer(1, "Chaos Core", 696_000, 1)]

with Game({4: A_SET, 3: B_CORE}) as g:
    got = gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check(got == 740_000 - 696_000,
      f"per-unit difference, row 1 both sides: expected 44,000 got {got!r}")
check(got == 44_000, "and it is 44,000, not the 6,704,000 raw prices give")

# Row 1 stays row 1 even when a later row is cheaper per unit. That is the
# spec's rule and this test exists to keep anyone from "improving" it.
with Game({4: [offer(1, "X 10", 7_400_000, 10),
               offer(2, "X 999", 700_000_000, 999)],
           3: B_CORE}) as g:
    got = gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check(got == 740_000 - 696_000,
      "row 2 at 700,700/unit is CHEAPER, and is still ignored: row 1 is the "
      f"rule. got {got!r}")

with Game({1: [offer(1, "a", 500, 1)]}) as g:
    got = gpd.get_price_diff(1, 1, in_shop=True, verbose=False)
check(got == 0, "the same slot both sides is a measured 0, a real answer")

print("=" * 70)
print("every failure is None, never 0")
print("=" * 70)

with Game({4: A_SET, 3: []}) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "a slot with no offers gives None, not 0")
with Game({4: [], 3: B_CORE}) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "and it is None whichever side failed")
with Game({4: [offer(2, "not row 1", 100, 1)], 3: B_CORE}) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "a result with no row 1 gives None rather than another row")
with Game({4: [offer(1, "bad", 0, 1)], 3: B_CORE}) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "an unreadable price gives None")
with Game({4: A_SET, 3: B_CORE}, sort=False) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "an unconfirmed sort refuses: 'row 1 is the cheapest' does not hold")
    check("search:4" not in g.events,
          "and it refuses BEFORE searching, not after")
with Game({4: A_SET, 3: B_CORE}, purchase_tab=False) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "no Purchase tab, no prices")
with Game({4: A_SET, 3: B_CORE}, window_open=False, can_open=False) as g:
    check(gpd.get_price_diff(4, 3, in_shop=False, verbose=False) is None,
          "a shop that will not open gives None")

for bad in (0, 11, -1, None, "4"):
    with Game({4: A_SET, 3: B_CORE}) as g:
        check(gpd.get_price_diff(bad, 3, in_shop=True, verbose=False) is None,
              f"slot {bad!r} is not a favourite slot")
        check(not g.events,
              f"and NOTHING was clicked for slot {bad!r}")

print("=" * 70)
print("the game is left as it was found")
print("=" * 70)

with Game({4: A_SET, 3: B_CORE}, window_open=False) as g:
    gpd.get_price_diff(4, 3, in_shop=False, verbose=False)
check("open_shop" in g.events and "leave_shop" in g.events,
      "a shop this call opened is closed again")
check(g.events.index("open_shop") < g.events.index("leave_shop"),
      "and closed after the work, not before")

with Game({4: A_SET, 3: B_CORE}, window_open=True, register=True) as g:
    gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check("leave_shop" not in g.events,
      "a shop the CALLER opened is not closed underneath them")
check("restore_register" in g.events,
      "but the Register tab is put back -- the listings table only exists "
      "there, and a caller left on Purchase would scroll the offers instead")

with Game({4: A_SET, 3: B_CORE}, window_open=True, register=False) as g:
    gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check("restore_register" not in g.events,
      "a caller already on Purchase is left on Purchase")

with Game({4: A_SET, 3: B_CORE}, window_open=False, sort=False) as g:
    gpd.get_price_diff(4, 3, in_shop=False, verbose=False)
check("leave_shop" in g.events,
      "the shop is closed even when the call REFUSES partway -- the tidy-up "
      "runs on every path, not just the happy one")

print("=" * 70)
print("the hint is checked, not trusted")
print("=" * 70)

with Game({4: A_SET, 3: B_CORE}, window_open=False) as g:
    got = gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check(got == 44_000, "in_shop=True with the window SHUT still works...")
check("open_shop" in g.events,
      "...because the window is measured and opened anyway, rather than "
      "clicking favourite coordinates into the 3D world")

with Game({4: A_SET, 3: B_CORE}, window_open=True) as g:
    gpd.get_price_diff(4, 3, in_shop=False, verbose=False)
check("open_shop" not in g.events,
      "in_shop=False with the window OPEN does not reopen it")

print()
print("-" * 70)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
