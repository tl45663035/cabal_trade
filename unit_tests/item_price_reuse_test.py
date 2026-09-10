import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

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


m.forget_item_prices()
check(m._item_price_reusable(7, attempt=1) is None,
      "nothing remembered means the price must be read")

m.note_item_price(7, FakeOffer(240_000))
kept = m._item_price_reusable(7, attempt=1)
check(kept is not None and kept.unit == 240_000,
      f"a price read moments ago is reused on the next buy, got {kept}")

check(m._item_price_reusable(7, attempt=2) is None,
      "a RETRY must never reuse it -- a retry means the row was bought out "
      "from under us, which is the market moving, which is when a stale "
      "baseline is least worth trusting. This is why the previous cache was "
      "removed.")
check(m._item_price_reusable(7, attempt=5) is None,
      "and that holds for every later attempt too")

check(m._item_price_reusable(9, attempt=1) is None,
      "a different item has its own price, not this one's")

m._ITEM_PRICE_CACHE[7]["at"] -= m.ITEM_PRICE_REUSE_SECONDS + 1
check(m._item_price_reusable(7, attempt=1) is None,
      f"past {m.ITEM_PRICE_REUSE_SECONDS:g}s it is re-read: a restock can run "
      f"for minutes and the market is only steady over seconds")

m.note_item_price(7, FakeOffer(240_000))
m.forget_item_prices()
check(m._item_price_reusable(7, attempt=1) is None,
      "forget_item_prices really clears it")

check(m.ITEM_PRICE_REUSE_SECONDS <= 300,
      f"the reuse window must stay short -- it is justified by rounds being "
      f"~30s apart, not by the market being stable for long. got "
      f"{m.ITEM_PRICE_REUSE_SECONDS:g}s")


import inspect

src = inspect.getsource(m.buy_cheapest_set_detail) \
    if hasattr(m, "buy_cheapest_set_detail") else ""
if not src:
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

item_at = src.rfind("run_favourite_search(item_slot")
set_at = src.rfind("run_favourite_search(set_slot")
check(set_at > item_at,
      "the SET search must be the last one before the buy -- buy_offer takes "
      "row 1 of whatever search ran most recently, and an item search left "
      "last would have it buying from the wrong list")

until_src = inspect.getsource(m.buy_sets_until)
check("forget_item_prices()" in until_src,
      "each restock must start from a fresh price rather than inheriting one "
      "from an earlier item or an earlier cycle")


print(f"item_price_reuse_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
