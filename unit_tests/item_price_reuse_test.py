"""The loose-item price is reused between buys, never across a retry.

Each buy round used to search BOTH favourites: the loose item, to compute the
saving, and the Set, which is what is actually bought. Measured 2026-08-09 that
was 22 of every 39 seconds in a buy round, and four consecutive rounds read the
loose item at an identical 240,000.00 to recompute an identical 45,762.00
saving. Fifteen rounds is about 165 seconds spent re-reading a number that did
not move.

WHY REUSE IS SAFE HERE, and only here. A stale item price can mislead in one
direction only -- by being too HIGH, which overstates the saving. A Core price
that has RISEN since simply means the Cores this restock produces sell for
more. For the fall to matter it must be nearly 20% inside a single restock:
against Sets at 194,238 the loose item stood at 240,000, so anything above
194,238 is still profitable and anything above 199,238 still clears the 5,000
threshold.

WHY A RETRY IS DIFFERENT, and why the previous version of this cache was
deleted. A retry happens because the row we wanted was bought out from under
us -- exactly the moment the market IS moving, and exactly when a baseline
measured a minute ago is least worth trusting. So a retry always re-reads.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


class FakeOffer:
    def __init__(self, unit):
        self.unit = unit


# -- the rule itself --------------------------------------------------------
m.forget_item_prices()
check(m._item_price_reusable(7, attempt=1) is None,
      "nothing remembered means the price must be read")

m.note_item_price(7, FakeOffer(240_000))
kept = m._item_price_reusable(7, attempt=1)
check(kept is not None and kept.unit == 240_000,
      f"a price read moments ago is reused on the next buy, got {kept}")

# THE ONE THAT MATTERS.
check(m._item_price_reusable(7, attempt=2) is None,
      "a RETRY must never reuse it -- a retry means the row was bought out "
      "from under us, which is the market moving, which is when a stale "
      "baseline is least worth trusting. This is why the previous cache was "
      "removed.")
check(m._item_price_reusable(7, attempt=5) is None,
      "and that holds for every later attempt too")

# Per slot: one Core's price says nothing about another's.
check(m._item_price_reusable(9, attempt=1) is None,
      "a different item has its own price, not this one's")

# Age still bounds it, so a long restock re-reads eventually.
m._ITEM_PRICE_CACHE[7]["at"] -= m.ITEM_PRICE_REUSE_SECONDS + 1
check(m._item_price_reusable(7, attempt=1) is None,
      f"past {m.ITEM_PRICE_REUSE_SECONDS:g}s it is re-read: a restock can run "
      f"for minutes and the market is only steady over seconds")

# A restock starts from a clean baseline rather than inheriting another item's.
m.note_item_price(7, FakeOffer(240_000))
m.forget_item_prices()
check(m._item_price_reusable(7, attempt=1) is None,
      "forget_item_prices really clears it")

check(m.ITEM_PRICE_REUSE_SECONDS <= 300,
      f"the reuse window must stay short -- it is justified by rounds being "
      f"~30s apart, not by the market being stable for long. got "
      f"{m.ITEM_PRICE_REUSE_SECONDS:g}s")


# -- the wiring -------------------------------------------------------------
import inspect  # noqa: E402

src = inspect.getsource(m.buy_cheapest_set_detail) \
    if hasattr(m, "buy_cheapest_set_detail") else ""
if not src:
    # Whatever the search-and-compare function is called, find it by content.
    for name in dir(m):
        fn = getattr(m, name)
        if callable(fn) and getattr(fn, "__module__", "") == "trade":
            try:
                body = inspect.getsource(fn)
            except (OSError, TypeError):
                continue
            if "_item_price_reusable(" in body:
                src = body
                break
check("_item_price_reusable(" in src,
      "the buy path must consult the cache, or none of the above is reached")
check("note_item_price(" in src,
      "and must record every fresh read, or it can never reuse one")

# THE ORDERING buy_offer DEPENDS ON. The Set search must be the LAST search
# before the buy: buy_offer refuses a Buy that is not row 1 of a search that
# just ran, and the Purchase tab never clears its results, so rows left from an
# earlier search look exactly like fresh ones. Skipping the item search must
# not have moved the Set search away from last.
item_at = src.rfind("run_favourite_search(item_slot")
set_at = src.rfind("run_favourite_search(set_slot")
check(set_at > item_at,
      "the SET search must be the last one before the buy -- buy_offer takes "
      "row 1 of whatever search ran most recently, and an item search left "
      "last would have it buying from the wrong list")

# A restock must clear the baseline before its first round.
until_src = inspect.getsource(m.buy_sets_until)
check("forget_item_prices()" in until_src,
      "each restock must start from a fresh price rather than inheriting one "
      "from an earlier item or an earlier cycle")


print(f"item_price_reuse_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
