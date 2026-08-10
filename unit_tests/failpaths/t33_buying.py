"""Choosing what to buy on the Purchase tab.

Three traps, all of them met on the live shop on 2026-08-07 while buying a
Force Core Set (High):

  * The table sorts by price PER ITEM, not by the listing total. The totals ran
    11.6M, 23.2M, 29.8M ... and then 5.7M LAST. Taking the smallest bill buys
    the worst value in the table, which defeats the entire point -- a Set is
    only worth buying because it is cheaper per core than the loose item.

  * That per-item figure is the bundle price divided by the pack size, and the
    game prices bundles in whole Alz, so it carries a rounding remainder that
    is not a price. Comparing the floats bought row 2 over row 1 for 8,614,760
    Alz more outlay, to save 38 Alz across the stack.

  * A clipped price read looks exactly like the bargain of the day, and sorts
    to the top BECAUSE it is too small. One row read 444,281 for 39 items --
    11,391 each against 187,278 everywhere else -- having lost the leading "7,"
    of 7,444,281.

And one that hides worse than any of them: the Purchase tab never clears its
results, so a search that fails to run leaves the previous item's rows on
screen. Comparing the Set against itself reported "saving 0.00/each" and
declined to buy -- a refusal that looks completely reasonable and is measuring
nothing.
"""
from harness import check, section, summary

import trade


def _raises(fn):
    try:
        fn()
    except Exception:
        return True
    return False


def offer(row, name, price, pack):
    return trade.Offer(row=row, name=name, price=price, pack=pack,
                       y=340 + (row - 1) * 76)


# The table as it stood at the moment of the mistake.
LIVE = [
    offer(1, "Force Core Set (High) X 124", 23_222_500, 124),
    offer(2, "Force Core Set (High) X 170", 31_837_260, 170),
    offer(3, "Force Core Set (High) X 231", 43_261_218, 231),
    offer(4, "Force Core Set (High) X 283", 53_000_000, 283),
    offer(5, "Force Core Set (High) X 39",   7_444_281,  39),
]


# ===========================================================================
section("pack sizes come out of the name")

for name, want in (("Force Core Set (High) X 62", 62),
                   ("Force Core Set (High) X 1,250", 1250),
                   ("Force Core(High)", 1),
                   ("Force Core Set (High)", 1),
                   ("Upgrade Core Set (Ultimate) X 1", 1)):
    got = trade.pack_size(name)
    check(f"pack_size({name!r}) == {want}", got == want, f"got {got}")

check("a Set's per-item price is the bundle divided by its size",
      abs(offer(1, "x X 124", 23_222_500, 124).unit - 187_278.226) < 0.01,
      f"{offer(1, 'x X 124', 23_222_500, 124).unit}")


# ===========================================================================
section("the first row is taken, full stop")

# The table arrives sorted Price: Low to High. Two earlier versions tried to
# improve on that ordering and both were wrong -- "smallest total" bought the
# worst value in the table, and "lowest per item" compared floats whose
# fractional part is a rounding remainder, paying 8,614,760 more to save 38.
pick = trade.cheapest_listing(LIVE)
check("takes row 1", pick.row == 1, f"picked row {pick.row} at {pick.price:,}")
check("...which is the cheapest listing as the game ordered it",
      pick.price == 23_222_500, f"{pick.price:,}")

# Order is the game's, not ours: even when a later row looks better on paper.
odd = [offer(1, "Set X 10", 2_000_000, 10),        # 200,000 each
       offer(2, "Set X 100", 18_000_000, 100)]     # 180,000 each
check("a better-looking later row does NOT displace row 1",
      trade.cheapest_listing(odd).row == 1,
      f"row {trade.cheapest_listing(odd).row} -- reinterpreting the sort is "
      f"exactly what went wrong twice")

check("an empty table yields nothing to buy",
      trade.cheapest_listing([]) is None, "")


# ===========================================================================
section("a clipped price is not a bargain")

clipped = LIVE + [offer(6, "Force Core Set (High) X 39", 444_281, 39)]
kept = trade.credible_offers(clipped)
check("the clipped row is discarded", all(o.price != 444_281 for o in kept),
      f"{[(o.row, o.price) for o in kept]} -- 11,391/item against 187,278 "
      f"everywhere else is a lost leading digit, not a find")
# NOTE: the selector no longer filters -- it takes row 1 unconditionally. A
# clipped read is caught downstream instead, where buy_offer compares the
# confirm dialog's own price against the row before confirming. That is the
# price that actually leaves the account, so it is the right place to check.
check("credible_offers still identifies it, for callers that want to know",
      all(o.price != 444_281 for o in trade.credible_offers(clipped)),
      f"{[(o.row, o.price) for o in trade.credible_offers(clipped)]}")
check("...while the genuine rows survive", len(kept) == len(LIVE),
      f"kept {len(kept)} of {len(LIVE)}")

# With too few rows to form a median, nothing is thrown away.
two = [offer(1, "Set X 10", 100, 10), offer(2, "Set X 10", 1_000_000, 10)]
check("two rows are both kept - a median of two proves nothing",
      len(trade.credible_offers(two)) == 2, "")


# ===========================================================================
section("stale results must not pass as a search")

set_rows = [offer(i, f"Force Core Set (High) X {i * 10}", i * 1_872_780, i * 10)
            for i in range(1, 6)]
item_rows = [offer(i, "Force Core(High)", 209_800, 1) for i in range(1, 6)]

check("Set rows match the Set slot", trade.offers_match_slot(8, set_rows), "")
check("Set rows do NOT match the loose-item slot",
      not trade.offers_match_slot(7, set_rows),
      "a Set's name CONTAINS its parent's, so a substring test would pass "
      "here and compare the Set against itself -- reported as 'saving 0.00'")
check("loose-item rows match the item slot",
      trade.offers_match_slot(7, item_rows), "")
check("loose-item rows do NOT match the Set slot",
      not trade.offers_match_slot(8, item_rows), "")
check("nothing on screen matches nothing", not trade.offers_match_slot(7, []), "")


# ===========================================================================
section("the favourites are paired item-then-Set")

for item_slot in (1, 3, 5, 7, 9):
    partner = trade.favourite_set_slot(item_slot)
    check(f"slot {item_slot} pairs to slot {item_slot + 1}",
          partner == item_slot + 1,
          f"got {partner} for {trade.FAVOURITE_SLOTS[item_slot]!r}")
for set_slot in (2, 4, 6, 8, 10):
    check(f"slot {set_slot} is a Set, with no Set of its own",
          trade.favourite_set_slot(set_slot) is None,
          f"got {trade.favourite_set_slot(set_slot)}")

check("slot positions are an even series",
      all(trade.favourite_slot_point(s + 1)[0]
          - trade.favourite_slot_point(s)[0] == trade.FAVOURITE_PITCH
          for s in range(1, trade.FAVOURITE_COUNT)), "")
check("an out-of-range slot is refused, not clamped",
      _raises(lambda: trade.favourite_slot_point(0))
      and _raises(lambda: trade.favourite_slot_point(11)),
      "a clamped slot would click a real button that nobody chose")




# ===========================================================================
section("nothing is clicked unless the Purchase tab is really there")

# On 2026-08-07 a capture loop checked the Trade window ONCE and then clicked
# favourite coordinates eighty times. The window closed partway through, and
# every later click went into the 3D world as a move order: the character
# walked away from the NPC, an item tooltip opened, and the run was lost. The
# clicks were all correct -- for a window that was no longer there.
#
# So the preconditions are re-checked before EVERY click, and all four must
# hold: the window is open, the area is actually covered by a panel (the text
# search alone can be fooled by the world), the PURCHASE tab is showing, and
# the sort is Price: Low to High -- without which "row 1 is the cheapest" is
# simply false.
from harness import Harness as _BH, empty_panel as _bep, make_row as _bmk, run as _brun


class Purchase(_BH):
    """A Trade window on the Purchase tab, with each precondition switchable."""

    def __init__(self, window=True, covered=True, tab=True, sorted_ok=True, **kw):
        super().__init__(rows=[_bmk(1, "x", price=1, qty=1)], panel=_bep(), **kw)
        self.window, self.covered, self.tab, self.sorted_ok = \
            window, covered, tab, sorted_ok

    def install(self):
        out = super().install()
        h = self
        trade.trade_window_open = lambda *a, **k: h.window
        trade.panel_covers_trade_area = lambda *a, **k: h.covered
        trade.purchase_tab_open = lambda *a, **k: h.tab
        trade.purchase_sorted_low_to_high = lambda *a, **k: h.sorted_ok
        return out


for label, kwargs, want in (
    ("everything in order",        {}, True),
    ("the window is shut",         {"window": False}, False),
    ("the area is still animating", {"covered": False}, False),
    ("the Register tab is showing", {"tab": False}, False),
    ("the sort is not low-to-high", {"sorted_ok": False}, False),
):
    h = Purchase(**kwargs, verbose=False)
    with h:
        got, exc = _brun(trade.purchase_ready, False)
        check(f"purchase_ready: {label} -> {want}", got is want,
              f"got {got!r} {exc!r}")

# The property that actually matters: a refusal sends NO input.
for label, kwargs in (("window shut", {"window": False}),
                      ("area animating", {"covered": False}),
                      ("wrong tab", {"tab": False}),
                      ("wrong sort", {"sorted_ok": False})):
    h = Purchase(**kwargs, verbose=False)
    with h:
        _brun(trade.run_favourite_search, 8, 0.0, 1, False)
        clicks = [c for c in h.calls if c[0] in ("click", "ctrl_click")]
        check(f"a favourite sweep sends no click when {label}", clicks == [],
              f"{len(clicks)} click(s) -- each one is a move order into the "
              f"game world")

    h = Purchase(**kwargs, verbose=False)
    with h:
        target = trade.Offer(row=1, name="Set X 10", price=1_000_000,
                             pack=10, y=340)
        got, exc = _brun(trade.buy_offer, target, 8.0, False)
        clicks = [c for c in h.calls if c[0] in ("click", "ctrl_click")]
        check(f"a buy sends no click when {label}", clicks == [],
              f"{len(clicks)} click(s)")
        check(f"...and reports why when {label}",
              isinstance(got, tuple) and got[0] is False, f"{got!r} {exc!r}")

# And with everything in order it does still click.
h = Purchase(verbose=False)
with h:
    _brun(trade.run_favourite_search, 8, 0.0, 1, False)
    clicks = [c for c in h.calls if c[0] == "click"]
    check("a sweep DOES click when every precondition holds", len(clicks) >= 1,
          f"{len(clicks)} click(s) -- the guard must not block ordinary work")


section("every attempt re-searches BOTH sides, and only row 1 is ever bought")
# A retry happens because the row we wanted was bought out from under us --
# which is exactly when the market is moving and a baseline measured a minute
# ago is least worth trusting. The loose-item price used to be read once,
# before the retry loop, and reused: a fresh Set price judged against a stale
# item price can invent a saving that no longer exists.
#
# The search is also what makes "row 1" mean anything at all. The Purchase tab
# never clears its results, so rows left from an earlier search look exactly
# like fresh ones.


class _Market:
    """Counts searches per favourite slot and records which row was bought."""

    def __init__(self, sold_out_times=0):
        self.searches, self.bought = [], []
        self.sold_out_times = sold_out_times

    def search(self, slot, settle=3.0, tries=2, verbose=True):
        self.searches.append(slot)
        if slot % 2 == 0:                      # the Set slot: cheaper per item
            return [trade.Offer(1, "Force Core Set (High) X 10", 1_870_000, 10, 340),
                    trade.Offer(2, "Force Core Set (High) X 10", 1_900_000, 10, 416)]
        return [trade.Offer(1, "Force Core(High)", 209_800, 1, 340)]

    def buy(self, offer, want=1, timeout=8.0, report=None, verbose=True):
        # `report` mirrors buy_offer's signature. Without it this fake raised
        # TypeError the moment the parameter was added to the real function,
        # and every check from here down never ran -- a whole suite silently
        # skipped while the runner reported a crash rather than a failure.
        self.bought.append(offer.row)
        if report is not None:
            report.setdefault("take", want)
            report.setdefault("items", want * max(1, getattr(offer, "pack", 1)))
        if len(self.bought) <= self.sold_out_times:
            return False, "the listing sold out before the click"
        return True, ""


for _sold_out in (0, 1, 2):
    _m = _Market(_sold_out)
    # Each iteration is a separate restock, so it starts from a fresh market.
    # buy_cheapest_set does not clear the loose-item memo itself -- production
    # never calls it directly, buy_sets_until does and clears it there -- so
    # without this the price from the previous iteration decides this one, and
    # the first attempt skips the item search entirely.
    trade.forget_item_prices()
    _saved = (trade.run_favourite_search, trade.buy_offer,
              trade.favourite_set_slot)
    trade.run_favourite_search, trade.buy_offer = _m.search, _m.buy
    trade.favourite_set_slot = lambda s: s + 1
    try:
        _ok = trade.buy_cheapest_set(7, verbose=False)
    finally:
        (trade.run_favourite_search, trade.buy_offer,
         trade.favourite_set_slot) = _saved

    _tries = min(_sold_out + 1, trade.BUY_RETRY_ATTEMPTS)
    check(f"{_sold_out} sold-out: the buy still succeeds", _ok is True,
          f"got {_ok!r}")
    check(f"{_sold_out} sold-out: both sides searched on every attempt",
          _m.searches == [7, 8] * _tries,
          f"got {_m.searches}, wanted {[7, 8] * _tries}")
    check(f"{_sold_out} sold-out: the item side is never reused stale",
          _m.searches.count(7) == _tries,
          f"the loose item was searched {_m.searches.count(7)} time(s) "
          f"across {_tries} attempt(s)")
    check(f"{_sold_out} sold-out: only row 1 is ever bought",
          set(_m.bought) == {1}, f"rows {_m.bought}")
    check(f"{_sold_out} sold-out: one buy click per attempt",
          len(_m.bought) == _tries, f"{len(_m.bought)} buy(s)")


raise SystemExit(summary())
