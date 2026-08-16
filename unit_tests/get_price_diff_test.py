"""get_price_diff: row 1 always, per unit always, None never 0.

DRIVES NOTHING. Every module that would click is replaced before the function
under test can reach it.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from cabal import geometry as geo         # noqa: E402
from cabal.layout import Layout           # noqa: E402
from cabal.purchase import Offer          # noqa: E402
import get_price_diff as gpd              # noqa: E402

PASS = FAIL = 0

LAYOUT = Layout(screen=(2560, 1440), origin=(10, 30), scale=1.0,
                client=(0, 23, 2560, 1392))


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def rule(title):
    print("=" * 70)
    print(title)
    print("=" * 70)


def offer(row, name, price, pack, available=1):
    return Offer(row=row, name=name, price=price, pack=pack,
                 available=available)


class Game:
    """Stands in for every cabal.* call get_price_diff makes."""

    def __init__(self, results, window_open=True, register=False,
                 purchase_tab=True, sort=True, can_open=False,
                 layout=LAYOUT):
        self.results = results
        self.window_open = window_open
        self.register = register
        self.purchase_tab = purchase_tab
        self.sort = sort
        self.can_open = can_open
        self.layout = layout
        self.events = []
        self._saved = {}

    def __enter__(self):
        patches = {
            # The layout is cached across calls in the real module, so the
            # test replaces the accessor rather than the calibration beneath
            # it -- otherwise the second test in a run would reuse the first
            # test's layout.
            (gpd, "_current_layout"): lambda *a, **k: (self.layout, None),
            (gpd.shop, "read_state"): self._state,
            (gpd.shop, "trade_window_open"): lambda *a, **k: self.window_open,
            (gpd.shop, "register_tab_open"): lambda *a, **k: self.register,
            (gpd.shop, "open_agent_shop"): self._open,
            (gpd.shop, "open_purchase_tab"): self._purchase,
            (gpd.shop, "open_register_tab"): self._restore,
            (gpd.shop, "close_shop"): self._close,
            (gpd.purchase, "set_sort_low_to_high"): self._sort,
            (gpd.purchase, "run_favourite_search"): self._search,
        }
        for (mod, name), fn in patches.items():
            self._saved[(mod, name)] = getattr(mod, name)
            setattr(mod, name, fn)
        return self

    def __exit__(self, *exc):
        for (mod, name), fn in self._saved.items():
            setattr(mod, name, fn)
        return False

    def _state(self, *a, **k):
        return gpd.shop.ShopState(
            window_open=self.window_open,
            purchase_tab=self.window_open and self.purchase_tab,
            register_tab=self.window_open and self.register,
            sorted_low_to_high=self.sort)

    def _open(self, *a, **k):
        self.events.append("open_shop")
        if self.can_open:
            self.window_open = True
        return self.can_open

    def _purchase(self, *a, **k):
        self.events.append("purchase_tab")
        if self.purchase_tab:
            self.register = False
        return self.purchase_tab

    def _restore(self, *a, **k):
        self.events.append("restore_register")
        self.register = True
        return True

    def _close(self, *a, **k):
        self.events.append("close_shop")
        self.window_open = False
        return True

    def _sort(self, *a, **k):
        self.events.append("sort")
        return self.sort

    def _search(self, layout, slot, *a, **k):
        self.events.append(f"search:{slot}")
        return list(self.results.get(slot, []))


rule("row 1 is selected by its row NUMBER, never by position")

rows = [offer(2, "second", 200, 1), offer(1, "first", 100, 1),
        offer(3, "third", 300, 1)]
check(gpd._row_one(rows).name == "first",
      "row 1 is found even when it is not first in the list -- read_offer_rows "
      "SKIPS rows it could not parse, so offers[0] is the first row that "
      "PARSED, not the first row on screen")
check(gpd._row_one([offer(2, "x", 1, 1)]) is None,
      "when row 1 did not read, NO row is substituted for it")
check(gpd._row_one([]) is None, "an empty result has no row 1")

rule("the difference is per unit")

A_SET = [offer(1, "Chaos Core Set X 10", 7_400_000, 10),
         offer(2, "Chaos Core Set X 999", 745_000_000, 999)]
B_CORE = [offer(1, "Chaos Core", 696_000, 1)]

with Game({4: A_SET, 3: B_CORE}) as g:
    got = gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check(got == 44_000,
      f"740,000/unit - 696,000/unit = 44,000, not the 6,704,000 the raw "
      f"prices give. got {got!r}")

with Game({4: [offer(1, "X 10", 7_400_000, 10),
               offer(2, "X 999", 700_000_000, 999)], 3: B_CORE}) as g:
    got = gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check(got == 44_000,
      "row 2 at 700,700/unit is CHEAPER per unit and is still ignored: "
      f"row 1 is the rule. got {got!r}")

with Game({1: [offer(1, "a", 500, 1)]}) as g:
    check(gpd.get_price_diff(1, 1, in_shop=True, verbose=False) == 0,
          "the same slot both sides is a measured 0, which is a real answer")

with Game({4: B_CORE, 3: A_SET}) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) == -44_000,
          "the sign follows A - B and may be negative")

rule("every failure is None, never 0")

cases = [
    ("a slot with no offers", dict(results={4: A_SET, 3: []})),
    ("the other slot empty", dict(results={4: [], 3: B_CORE})),
    ("no row 1 in the results",
     dict(results={4: [offer(2, "not row 1", 100, 1)], 3: B_CORE})),
    ("an unreadable price", dict(results={4: [offer(1, "bad", 0, 1)], 3: B_CORE})),
    ("a pack that did not read",
     dict(results={4: [offer(1, "bad", 100, 0)], 3: B_CORE})),
    ("no Purchase tab", dict(results={4: A_SET, 3: B_CORE}, purchase_tab=False)),
    ("an unconfirmed sort", dict(results={4: A_SET, 3: B_CORE}, sort=False)),
]
for why, kwargs in cases:
    with Game(**kwargs) as g:
        check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
              f"{why} gives None, not 0")

with Game({4: A_SET, 3: B_CORE}, sort=False) as g:
    gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check("search:4" not in g.events,
      "an unconfirmed sort refuses BEFORE searching, not after")

with Game({4: A_SET, 3: B_CORE}, window_open=False, can_open=False) as g:
    check(gpd.get_price_diff(4, 3, in_shop=False, verbose=False) is None,
          "a shop that cannot be opened gives None")

with Game({4: A_SET, 3: B_CORE}, layout=None) as g:
    check(gpd.get_price_diff(4, 3, in_shop=True, verbose=False) is None,
          "an uncalibrated window gives None -- no coordinate means anything "
          "until the window has been located")
    check(not g.events, "and NOTHING was clicked")

for bad in (0, 11, -1, None, "4"):
    with Game({4: A_SET, 3: B_CORE}) as g:
        check(gpd.get_price_diff(bad, 3, in_shop=True, verbose=False) is None,
              f"slot {bad!r} is not a favourite slot")
        check(not g.events, f"and NOTHING was clicked for slot {bad!r}")

rule("the game is left as it was found")

with Game({4: A_SET, 3: B_CORE}, window_open=False, can_open=True) as g:
    gpd.get_price_diff(4, 3, in_shop=False, verbose=False)
check("open_shop" in g.events and "close_shop" in g.events,
      "a shop this call opened is closed again")
check(g.events.index("open_shop") < g.events.index("close_shop"),
      "and closed after the work, not before")

with Game({4: A_SET, 3: B_CORE}, window_open=True, register=True) as g:
    gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check("close_shop" not in g.events,
      "a shop the CALLER opened is not closed underneath them")
check("restore_register" in g.events,
      "but the Register tab is put back -- a caller left on Purchase would "
      "scroll the OFFERS when it went to read listings")

with Game({4: A_SET, 3: B_CORE}, window_open=True, register=False) as g:
    gpd.get_price_diff(4, 3, in_shop=True, verbose=False)
check("restore_register" not in g.events,
      "a caller already on Purchase is left on Purchase")

with Game({4: A_SET, 3: B_CORE}, window_open=False, can_open=True,
          sort=False) as g:
    gpd.get_price_diff(4, 3, in_shop=False, verbose=False)
check("close_shop" in g.events,
      "the shop is closed even when the call REFUSES partway -- the tidy-up "
      "runs on every path, not just the happy one")

rule("the in_shop hint is checked, not trusted")

with Game({4: A_SET, 3: B_CORE}, window_open=False, can_open=True) as g:
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
