import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import os as _os_guard
import sys as _sys_guard
_sys_guard.path.insert(0, _os_guard.path.dirname(
    _os_guard.path.abspath(__file__)))
import _no_input_guard

import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def summary():
    print(f"\n{checks} checks, {len(failures)} FAILED")
    for f in failures:
        print(f"  FAIL: {f}")
    return 1 if failures else 0



UNIT, WANT = 688_339, 20
EXPECTED = UNIT * WANT


def drive_buy(price_reads):
    saved = {n: getattr(m, n) for n in
             ("purchase_ready", "purchase_confirm", "click", "type_number",
              "park_cursor", "grab", "get_alz", "time")}
    seq = list(price_reads)
    state = {"reads": 0, "balance": 500_000_000, "bought": False}

    class NoSleep:
        monotonic = staticmethod(saved["time"].monotonic)

        @staticmethod
        def sleep(_):
            return None

    def confirm(source=None):
        state["reads"] += 1
        price = seq[min(state["reads"], len(seq)) - 1]
        return {"buy": (100, 900), "cancel": (200, 900), "price": price,
                "text": f"{WANT} x Chaos Core", "qty": WANT, "qty_max": WANT}

    def clicked(x, y, *a, **k):
        if (x, y) == (100, 900):
            state["bought"] = True
            state["balance"] -= EXPECTED
        return True

    try:
        m.purchase_ready = lambda **k: True
        m.purchase_confirm = confirm
        m.click = clicked
        m.type_number = lambda *a, **k: True
        m.park_cursor = lambda *a, **k: None
        m.move_mouse = lambda *a, **k: True
        m.move_mouse_to_alz = lambda *a, **k: True
        m.get_alz = lambda *a, **k: state["balance"]
        m.time = NoSleep
        offer = m.Offer(row=1, name="Chaos Core", price=UNIT, pack=1,
                        y=400, available=WANT)
        m.note_favourite_search(m.CHAOS_CORE_SLOT, [offer])
        bought, why = m.buy_offer(offer, want=WANT, verbose=False)
        return bought, why, state["reads"]
    finally:
        for n, v in saved.items():
            setattr(m, n, v)
        m.BUY_HALTED, m._LAST_SEARCH = False, None


bought, why, reads = drive_buy([UNIT, UNIT, EXPECTED])
check(bought, f"a stale first Purchase Price must be retried, not refused; "
              f"got bought={bought} why={why!r}")
check(reads > 1, "the read-back must actually re-read when the price is stale")

bought, why, reads = drive_buy([UNIT * 204])
check(not bought, "a Purchase Price that never matches must refuse the order")
check("204" in why or "dialog says" in why,
      f"the refusal must name the mismatch it saw, got {why!r}")

bought, why, reads = drive_buy([EXPECTED])
check(bought, f"a correct price must buy, got {bought} {why!r}")
check(reads <= 2, f"a correct price must not spin the retry loop, {reads} reads")


src_names = m.chaos_pass.__code__.co_varnames
check("order_size" in src_names,
      "the chaos buy loop must not reuse the scope variable for the order size")

CHAOS_NAME = m.FAVOURITE_SLOTS[m.CHAOS_SET_SLOT]
rows = [type("R", (), {"index": i, "action": "change",
                       "name": f"{CHAOS_NAME} X 30", "price": 1, "qty": 1})()
        for i in (1, 2, 5)]
check([r.index for r in m.chaos_rows_in(rows, {1, 2})] == [1, 2],
      "chaos_rows_in must keep only the rows inside the boundary")
check([r.index for r in m.chaos_rows_in(rows, None)] == [1, 2, 5],
      "no scope means every chaos row, unchanged")
try:
    m.chaos_rows_in(rows, 7)
    widened = True
except TypeError:
    widened = False
check(not widened,
      "an integer where the row set belongs must fail loudly, not silently "
      "widen the boundary")


counts = m.core_row_counts([])
check(counts and all(n == 0 for n in counts.values()),
      "core_row_counts([]) reports every Core at zero -- the input this fix "
      "exists to stop restock_pass ever handing it")


def restock_decision(scope, visible_rows):
    saved = {n: getattr(m, n) for n in
             ("restock_is_armed", "await_rows", "enabled_buying_slots",
              "cached_rows_used", "restock_core", "shop_listing_pairs",
              "record")}
    seen = {"swept": False, "scoped": False, "skipped": True}

    def sweep(*a, **k):
        seen["swept"] = True
        return []

    def rec(label, *a, **k):
        if label == "restock.scoped":
            seen["scoped"] = True
        if label == "restock.scope_offscreen":
            seen["offscreen"] = True

    try:
        m.restock_is_armed = lambda *a, **k: True
        m.await_rows = lambda *a, **k: list(visible_rows)
        m.enabled_buying_slots = lambda: [1]
        m.cached_rows_used = lambda: 12
        m.shop_listing_pairs = sweep
        m.record = rec

        def core(slot, *a, **k):
            seen["skipped"] = False
            return {}
        m.restock_core = core
        m.restock_pass(timeout=0.0, verbose=False, scope=scope)
        return seen
    finally:
        for n, v in saved.items():
            setattr(m, n, v)


screen = [type("R", (), {"index": i, "action": "change", "name": "x"})()
          for i in range(1, 11)]

out = restock_decision([11, 12, 13], screen)
check(not out.get("scoped"),
      "a scope past row 10 must not take the scoped shortcut -- that is the "
      "path that calls every Core sold out without reading the shop")

out = restock_decision([1, 2], [])
check(out["skipped"] and not out.get("scoped"),
      "an unread table must restock NOTHING -- core_row_counts([]) reports "
      "every Core at zero, which reads as a total sell-out and buys a full "
      "target of each")

check(m.EXPECTED_ROWS == 10,
      f"the first screen is 10 rows; the scope test is written against that, "
      f"got {m.EXPECTED_ROWS}")


saved_added, saved_cache = m.BUY_ADDED_ROWS, m._ROWS_USED_CACHE
try:
    m.BUY_ADDED_ROWS = 0
    m.note_rows_used(20)
    m.note_rows_added(3)
    check(m.cached_rows_used() == 23,
          f"three rows listed after a count of 20 is 23, got "
          f"{m.cached_rows_used()}")

    m.note_shop_depth(18, 20)
    check(m.cached_rows_used() == 23,
          f"note_shop_depth must not be able to shrink the cached count "
          f"behind its baseline, got {m.cached_rows_used()}")
finally:
    m.BUY_ADDED_ROWS, m._ROWS_USED_CACHE = saved_added, saved_cache

import inspect
body = inspect.getsource(m.relist_rows)
check("note_shop_depth(" not in body,
      "relist_rows must re-anchor the count with note_rows_used, not mutate "
      "BUY_ADDED_ROWS behind the cache")

raise SystemExit(summary())
