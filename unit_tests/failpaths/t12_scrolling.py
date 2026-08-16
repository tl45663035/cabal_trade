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

    def _read_rows(self, source=None, words=None, **_):
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


section("'all' relists every listing, without being told how many")

for size in (4, 12, 25, 30):
    h = ScrollShop(shop_of(size))
    with h:
        h.patch("relist", spy(h))
        ok, exc = run(trade.relist_rows, [], all_rows=True)
        want = [f"Item {i:02d}" for i in range(1, size + 1)]
        check(f"all/{size}: batch succeeded", ok is True, f"got {ok!r} {exc!r}")
        check(f"all/{size}: every listing relisted, in order",
              targeted(h) == want,
              f"got {len(targeted(h))} of {size}: {targeted(h)}")

# The point of 'all': the count is never stated, so a shop that changes size
# between runs needs no edit.
h = ScrollShop(shop_of(30))
with h:
    h.patch("relist", spy(h))
    run(trade.relist_rows, [], all_rows=True)
    check("all: no row number was needed", len(targeted(h)) == 30,
          f"{len(targeted(h))}")

# Empty slots and sold rows are part of the sweep, not gaps in it.
h = ScrollShop(shop_of(20, **{"7": {"action": "register"},
                              "15": {"action": "receive"}}))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.relist_rows, [], all_rows=True)
    check("all: succeeded with an empty slot and a sold row",
          ok is True, f"got {ok!r} {exc!r}")
    check("all: the empty slot was skipped, not relisted",
          "Item 07" not in targeted(h), f"{targeted(h)}")
    check("all: the sold row WAS acted on", "Item 15" in targeted(h),
          f"{targeted(h)} -- a sale past row 10 is the money this mode exists "
          f"to collect")
    check("all: every other listing was relisted", len(targeted(h)) == 19,
          f"{len(targeted(h))} of 19 expected")


section("'all' parses from the command line and from --repeat")

check("wants_all_rows('all')", trade.wants_all_rows(["all"]) is True, "")
check("wants_all_rows('ALL')", trade.wants_all_rows(["ALL"]) is True,
      "case must not matter")
check("wants_all_rows('1-10')", trade.wants_all_rows(["1-10"]) is False, "")
check("wants_all_rows of nothing", trade.wants_all_rows([]) is False, "")
check("wants_all_rows('all','1')", trade.wants_all_rows(["all", "1"]) is False,
      "'all' mixed with rows is not 'all'")
check("parse_row_spec('all') is empty, not a crash",
      trade.parse_row_spec(["all"]) == [],
      f"{trade.parse_row_spec(['all'])!r}")
check("parse_row_spec('1-3') still works",
      trade.parse_row_spec(["1-3"]) == [1, 2, 3],
      f"{trade.parse_row_spec(['1-3'])!r}")

# --repeat drives run_sequence, which is where an unattended run lives. If
# 'all' were parsed there as a row list it would be empty and relist nothing,
# silently, for hours.
h = ScrollShop(shop_of(14))
with h:
    h.patch("relist", spy(h))
    ok, exc = run(trade.run_sequence, ["relist-rows all"])
    check("--repeat 'relist-rows all': succeeded", ok is True,
          f"got {ok!r} {exc!r}")
    check("--repeat 'relist-rows all': relisted all 14",
          len(targeted(h)) == 14,
          f"{len(targeted(h))} -- parsed as a row list this would be 0 and "
          f"the run would report success having done nothing")


section("--listings must survive an UNPRICED row")

# The reporting half of this feature, which crashed live: --listings scrolled
# the whole shop, enumerated all 30 rows, and then raised TypeError printing
# them, because an empty slot has no price and the inline conditional applied a
# width spec to None. The scroll was perfect and the output was a traceback.
check("money() formats a real price with grouping",
      trade.money(80_000_000) == "80,000,000", f"{trade.money(80_000_000)!r}")
check("money() renders no price as a blank marker",
      trade.money(None) == "-", f"{trade.money(None)!r}")
check("money(None) is a STRING, so a width spec applies to it",
      isinstance(trade.money(None), str),
      "the crash was `:>14` being applied to None")
check("money() takes a width spec without raising",
      f"{trade.money(None):>14}".strip() == "-", "")
check("money() renders zero, not a blank",
      trade.money(0) == "0",
      f"{trade.money(0)!r} -- a sold-out row is priced 0 and really is 0")
check("the blank marker is caller-chosen",
      trade.money(None, blank="unread") == "unread", "")

# Every row the enumerator can produce must format. An empty slot and a
# Premium marker both arrive with price AND qty unset.
for row in (make_row(1, "(empty)", action="register", price=None, qty=None),
            make_row(2, "Premium Exclusive Slot", action="register",
                     price=None, qty=None),
            make_row(3, "Force Core(High)", price=217_000, qty=250),
            make_row(4, "Siena's Unbinding Stone", action="receive",
                     price=70_000_000, qty=0)):
    line, exc = run(lambda r=row: f"{str(r.qty):>5} {trade.money(r.price):>14}")
    check(f"formats a {row.action} row named {row.name[:22]!r}",
          exc is None and line, f"{exc!r}")


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
