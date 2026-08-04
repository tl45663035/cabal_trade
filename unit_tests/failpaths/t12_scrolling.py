"""Reaching listings past the visible ten.

The shop holds thirty; the table shows ten. A sale sitting at row 25 was never
collected, because nothing in the loop could see it.

The two things that must both hold:

  * a batch asking for rows past ten reaches the RIGHT listing, by identity,
    after the view has been scrolled -- and refuses rather than guessing when
    it cannot tell which listing is which,
  * a batch asking only for rows 1-10 behaves EXACTLY as before: no
    enumeration, no scrolling, one table read. That path works and must not
    pay for this one.
"""
from harness import Harness, check, make_row, run, section, summary

import trade


def item(n, name=None, action="change", price=None, qty=None):
    """One shop listing. Distinct by default, so identity is unambiguous."""
    return {"name": name or f"Item {n:02d}",
            "action": action,
            "price": price if price is not None else 100_000 + n * 1_000,
            "qty": qty if qty is not None else 50 + n}


class ScrollShop(Harness):
    """A Harness whose shop is longer than the screen and really scrolls."""

    def __init__(self, shop, **kw):
        self.shop = [dict(s) for s in shop]
        self.view_top = 0
        super().__init__(rows=[], **kw)

    def _window(self):
        return self.shop[self.view_top:self.view_top + trade.EXPECTED_ROWS]

    def _visible(self):
        return [make_row(n, it["name"], action=it["action"],
                         price=it["price"], qty=it["qty"])
                for n, it in enumerate(self._window(), start=1)]

    def _read_rows(self, source=None):
        self.n_rows += 1
        fault = self.rows_fault.get(self.n_rows)
        if fault is not None:
            raise fault
        if self.loading:
            return []
        self.rows = self._visible()
        return list(self.rows)

    def _scroll(self, x, y, notches, **kwargs):
        # Negative notches scroll DOWN, and the list clamps at both ends.
        self.log("scroll_wheel", x, y, notches)
        highest = max(0, len(self.shop) - trade.EXPECTED_ROWS)
        self.view_top = max(0, min(highest, self.view_top - notches))

    def install(self):
        out = super().install()
        trade.scroll_wheel = self._scroll
        return out


def shop_of(n=30, **overrides):
    shop = [item(i) for i in range(1, n + 1)]
    for index, changes in overrides.items():
        shop[int(index) - 1].update(changes)
    return shop


def targeted(h):
    """The names relist() was actually asked to act on."""
    return [kw["expect"].name for name, _args, kw in h.calls
            if name == "relist_call" and kw.get("expect") is not None]


def spy(h):
    """Patch relist() to record what it was aimed at, and succeed."""
    def fake(row, *a, expect=None, **kw):
        h.log("relist_call", row, expect=expect)
        return trade.RELISTED
    return fake


section("rows 1-10 must not pay for scrolling at all")

h = ScrollShop(shop_of(30))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [1, 2, 3])
    check("fast path: batch succeeded", ok is True, f"got {ok!r} {exc!r}")
    check("fast path: NEVER scrolled", "scroll_wheel" not in h.names(),
          f"scrolled {h.names().count('scroll_wheel')} time(s) for rows 1-3 -- "
          f"the common case must not pay for the rare one")
    check("fast path: did not enumerate the shop",
          not h.said("enumerating the whole shop"), h.out()[:300])
    check("fast path: hit the right three listings",
          targeted(h) == ["Item 01", "Item 02", "Item 03"], f"{targeted(h)}")


section("a listing past the first screen is reached, by identity")

h = ScrollShop(shop_of(30))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [25])
    check("row 25: batch succeeded", ok is True, f"got {ok!r} {exc!r}")
    check("row 25: it scrolled", "scroll_wheel" in h.names(), h.names()[:20])
    check("row 25: enumerated first", h.said("enumerating the whole shop"),
          h.out()[:400])
    check("row 25: acted on Item 25 and nothing else",
          targeted(h) == ["Item 25"],
          f"{targeted(h)} -- acting on the wrong listing here cancels "
          f"something that was never asked for")


section("the sale at the bottom of the shop is now reachable")

# The concrete case from the live shop: 'Sold 1' with no Receive on screen.
h = ScrollShop(shop_of(30, **{"28": {"action": "receive"}}))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [28])
    check("sold row 28: batch succeeded", ok is True, f"got {ok!r} {exc!r}")
    check("sold row 28: acted on Item 28", targeted(h) == ["Item 28"],
          f"{targeted(h)}")


section("a mixed batch reaches both halves")

h = ScrollShop(shop_of(30))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [2, 17, 30])
    check("mixed: batch succeeded", ok is True, f"got {ok!r} {exc!r}")
    check("mixed: all three, in order",
          targeted(h) == ["Item 02", "Item 17", "Item 30"], f"{targeted(h)}")


section("out of range is refused, not clamped")

h = ScrollShop(shop_of(30))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [31])
    check("row 31: refused", ok is False, f"got {ok!r}")
    check("row 31: said how many the shop holds", h.said("30 listing"),
          h.out()[-300:])
    check("row 31: acted on nothing", targeted(h) == [], f"{targeted(h)}")


section("an indistinguishable shop is refused rather than guessed at")

# Every listing identical: measure_shift cannot pin the offset down, so the
# position of anything past the first screen is unknowable. Refusing is the
# only safe answer -- acting would cancel an arbitrary stack.
same = [item(i, name="Force Core(High)", price=210_000, qty=217)
        for i in range(1, 31)]
h = ScrollShop(same)
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [25])
    check("ambiguous shop: refused", ok is False, f"got {ok!r} {exc!r}")
    check("ambiguous shop: acted on nothing", targeted(h) == [],
          f"{targeted(h)} -- guessing here cancels the wrong stack")
    check("ambiguous shop: said why",
          h.said("could not") or h.said("refus"), h.out()[-400:])


raise SystemExit(summary())
